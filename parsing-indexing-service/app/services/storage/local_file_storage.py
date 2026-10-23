"""Filesystem-backed file storage, for an OpenShift persistent volume.

Point `LOCAL_STORAGE_PATH` at a mounted PVC and set `STORAGE_TYPE=local`.

Two deployment constraints follow from the design, both load-bearing:

* main-service and this service must mount **the same** volume, because
  only paths cross the REST boundary - one pod writes the upload, the other
  reads it. On OpenShift that means a **ReadWriteMany** PVC, not RWO: RWO binds
  to a single node and the second pod will fail to mount, or silently see a
  different empty volume if the storage class allows it.
* The pods run under an arbitrary UID in an OpenShift-assigned range, which is
  always a member of group 0. The volume must therefore be group-writable
  (`fsGroup` in the pod security context is the normal way to get this).
  Directories are created 0o770 here for the same reason (LOCAL_STORAGE_DIR_MODE).

Mirrors main-service-chatbot's copy of this file; see storage_service.py.
"""
import asyncio
import logging
import os
import shutil
from io import BytesIO
from pathlib import Path

from app.logic.storage_paths import volume_relative
from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)


class LocalFileStorage(FileStorageService):
    """Stores files as a directory tree under a single root."""

    def __init__(self, root_path: str, directory_mode: int = 0o770):
        """`directory_mode`: permission bits for the directories this store creates,
        group-writable by default (see above). LOCAL_STORAGE_DIR_MODE."""
        if not root_path:
            raise ValueError("root_path must be a non-empty string.")
        self._root = Path(root_path).resolve()
        self._directory_mode = directory_mode

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

    def _resolve(self, file_reference: str) -> Path:
        """Maps a store-relative path (or an old absolute URL) onto disk.

        Refuses anything that escapes the root. Store paths are built from
        user-supplied filenames, so "../../etc/passwd" and an absolute path are
        both reachable inputs and both have to be rejected here rather than
        trusted to whatever built the name.
        """
        candidate = (self._root / volume_relative(file_reference)).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError(f"Refusing to access a path outside the store: {file_reference!r}")
        return candidate

    def _make_directories(self, directory: Path) -> None:
        """Creates `directory` and any missing parents, each with the configured mode.

        Path.mkdir(parents=True, mode=...) applies the mode to the last directory
        only - the parents get the default - and mkdir's mode is masked by the
        process umask either way. So each new level is created and then chmod'ed,
        which is what keeps a directory one pod creates writable by the other."""
        missing = []
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        for level in reversed(missing):
            try:
                level.mkdir(mode=self._directory_mode)
            except FileExistsError:  # the other pod created it first; it is theirs to have set
                continue
            level.chmod(self._directory_mode)

    def _write(self, target: Path, data: bytes) -> None:
        self._make_directories(target.parent)
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
        await asyncio.to_thread(self._write, target, payload)
        relative = target.relative_to(self._root).as_posix()
        logger.info("Stored %s (%d bytes)", relative, len(payload))
        return relative

    async def download_file(self, file_name: str) -> BytesIO:
        target = self._resolve(file_name)
        try:
            return BytesIO(await asyncio.to_thread(target.read_bytes))
        except (FileNotFoundError, IsADirectoryError):
            raise FileNotFoundError(f"File not found: {file_name}") from None

    async def delete_file(self, file_name: str) -> bool:
        """Removes the file. Missing is success, matching the blob backends."""
        target = self._resolve(file_name)
        try:
            await asyncio.to_thread(target.unlink)
        except FileNotFoundError:
            pass
        except IsADirectoryError:
            await asyncio.to_thread(shutil.rmtree, target, True)
        return True

    async def exists(self, file_name: str) -> bool:
        try:
            target = self._resolve(file_name)
        except ValueError:
            return False
        return await asyncio.to_thread(target.is_file)
