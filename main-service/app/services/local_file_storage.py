"""Filesystem-backed file storage, for an OpenShift persistent volume.

This is the backend that replaces Azure Blob Storage on cluster: point
`LOCAL_STORAGE_PATH` at a mounted PVC and set `STORAGE_TYPE=local`.

Two deployment constraints follow from the design, both load-bearing:

* main-service and indexing-rpc-server must mount **the same** volume, because
  only paths cross the gRPC boundary - one pod writes the upload, the other
  reads it. On OpenShift that means a **ReadWriteMany** PVC, not RWO: RWO binds
  to a single node and the second pod will fail to mount, or silently see a
  different empty volume if the storage class allows it.
* The pods run under an arbitrary UID in an OpenShift-assigned range, which is
  always a member of group 0. The volume must therefore be group-writable
  (`fsGroup` in the pod security context is the normal way to get this).
  Directories are created 0o770 here for the same reason.

This file is duplicated in indexing-rpc-server; see storage_service.py.
"""
import asyncio
import logging
import os
import shutil
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, quote

from app.services.storage_service import FileStorageService

logger = logging.getLogger(__name__)


class LocalFileStorage(FileStorageService):
    """Stores files as a directory tree under a single root."""

    def __init__(self, root_path: str, public_base_url: Optional[str] = None):
        """
        :param root_path: Directory the store lives in. Created if absent.
        :param public_base_url: Optional base URL that serves `root_path` over
            HTTP (a sidecar, a route onto a static server). When unset,
            `get_download_url` returns None and callers degrade gracefully.
        """
        if not root_path:
            raise ValueError("root_path must be a non-empty string.")
        self._root = Path(root_path).resolve()
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None
        logger.info("LocalFileStorage configured at %s", self._root)

    async def initialize(self):
        """Creates the root directory if the volume is mounted empty."""
        await asyncio.to_thread(self._root.mkdir, parents=True, exist_ok=True)
        if not os.access(self._root, os.W_OK):
            raise RuntimeError(
                f"LOCAL_STORAGE_PATH '{self._root}' is not writable by this process "
                f"(uid={os.getuid()}, gid={os.getgid()}). On OpenShift the volume "
                f"needs to be group-writable - set fsGroup on the pod."
            )
        logger.info("LocalFileStorage ready at %s", self._root)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, file_reference: str) -> Path:
        """Maps a store-relative path (or an old absolute URL) onto disk.

        Refuses anything that escapes the root. Store paths are built from
        user-supplied filenames, so "../../etc/passwd" and an absolute path are
        both reachable inputs and both have to be rejected here rather than
        trusted to whatever built the name.
        """
        reference = (file_reference or "").strip()
        if not reference:
            raise ValueError("file_name must not be empty.")

        # File records written against Azure hold a full https URL. Keep them
        # readable by taking the path component.
        parsed = urlsplit(reference)
        if parsed.scheme in ("http", "https"):
            reference = parsed.path

        reference = reference.replace("\\", "/").lstrip("/")
        candidate = (self._root / reference).resolve()

        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError(f"Refusing to access a path outside the store: {file_reference!r}")
        return candidate

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _write(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o770)
        # Write beside the target and move into place, so a reader on the other
        # pod never sees a half-written file - the parse step reads what the
        # upload step wrote, and on a shared volume those are concurrent.
        staging = target.with_name(f".{target.name}.partial")
        staging.write_bytes(data)
        os.replace(staging, target)

    async def upload_file(self, file_data: BytesIO, file_name: str) -> str:
        """Writes the file and returns the store-relative path."""
        target = self._resolve(file_name)
        file_data.seek(0)
        payload = file_data.read()
        try:
            await asyncio.to_thread(self._write, target, payload)
        except Exception:
            logger.exception("Failed to write %s", target)
            raise
        relative = target.relative_to(self._root).as_posix()
        logger.info("Stored %s (%d bytes)", relative, len(payload))
        return relative

    async def download_file(self, file_name: str) -> BytesIO:
        """Reads the file into memory."""
        target = self._resolve(file_name)
        try:
            payload = await asyncio.to_thread(target.read_bytes)
        except FileNotFoundError:
            logger.warning("File not found in local store: %s", target)
            raise FileNotFoundError(f"File not found: {file_name}") from None
        except IsADirectoryError:
            raise FileNotFoundError(f"File not found: {file_name}") from None
        logger.info("Read %s (%d bytes)", target, len(payload))
        return BytesIO(payload)

    async def delete_file(self, file_name: str) -> bool:
        """Removes the file. Missing is success, matching the blob backends."""
        target = self._resolve(file_name)
        try:
            await asyncio.to_thread(target.unlink)
            logger.info("Deleted %s", target)
        except FileNotFoundError:
            logger.warning("Attempted to delete a file that does not exist: %s", target)
        except IsADirectoryError:
            await asyncio.to_thread(shutil.rmtree, target, True)
        return True

    async def exists(self, file_name: str) -> bool:
        try:
            target = self._resolve(file_name)
        except ValueError:
            return False
        return await asyncio.to_thread(target.is_file)

    def get_download_url(self, file_name: str) -> Optional[str]:
        """Only available when a static server is configured in front of the volume."""
        if not self._public_base_url:
            return None
        try:
            relative = self._resolve(file_name).relative_to(self._root).as_posix()
        except ValueError:
            return None
        return f"{self._public_base_url}/{quote(relative)}"
