"""OCI-registry-backed file storage using the OCI Distribution Spec (Quay target).

Each file is stored as a single-layer OCI artifact in a Quay repository.
The tag is the SHA-256 of the store-relative path — deterministic, collision-free,
and OCI-safe (64 lowercase hex chars). A manifest annotation carries the original
path so blobs are human-inspectable in the Quay UI.

No extra library required: uses httpx (already a dependency) against the OCI
Distribution Spec v1 API. Auth is the standard Bearer token challenge-response
flow that Quay and any compliant registry support.

This file is duplicated in main-service-chatbot; change both together.
"""
import hashlib
import json
import logging
import re
from io import BytesIO

import httpx

from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)

_EMPTY_CONFIG = b"{}"
_EMPTY_CONFIG_DIGEST = "sha256:" + hashlib.sha256(_EMPTY_CONFIG).hexdigest()
_MT_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
_MT_CONFIG = "application/vnd.oci.empty.v1+json"
_MT_BLOB = "application/octet-stream"
_ANN_PATH = "org.chatbot.store-path"


def _tag(store_path: str) -> str:
    """Deterministic, OCI-safe tag for a store-relative path."""
    return hashlib.sha256(store_path.encode()).hexdigest()


class QuayOrasStorage(FileStorageService):
    """
    Stores files as OCI artifacts in a Quay (or any OCI-compliant) registry.

    Required settings: QUAY_REGISTRY, QUAY_REPOSITORY, QUAY_USERNAME, QUAY_PASSWORD.
    Optional:  QUAY_INSECURE=true  — skip TLS verification for self-signed registries.
    """

    def __init__(
        self,
        registry: str,
        repository: str,
        username: str,
        password: str,
        insecure: bool = False,
    ):
        self._registry = registry.rstrip("/")
        self._repository = repository.strip("/")
        self._username = username
        self._password = password
        self._verify = not insecure
        self._base = f"https://{self._registry}/v2/{self._repository}"
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def initialize(self):
        # Short connect timeout so a misconfigured / unreachable registry
        # fails fast (10 s) rather than blocking for the full transfer timeout.
        self._client = httpx.AsyncClient(
            verify=self._verify,
            timeout=httpx.Timeout(timeout=120.0, connect=10.0),
        )
        logger.info("QuayOrasStorage ready: %s/%s", self._registry, self._repository)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _fetch_token(self, www_authenticate: str) -> str:
        """Exchange Basic credentials for a Bearer token at the auth service."""
        # WWW-Authenticate: Bearer realm="https://...",service="quay.io",scope="repository:...:pull,push"
        # Scope values contain commas (push,pull) so split-on-comma parsing is wrong;
        # use a regex that respects quoted values instead.
        parts: dict[str, str] = {
            m.group(1): m.group(2)
            for m in re.finditer(r'(\w+)="([^"]*)"', www_authenticate)
        }
        r = await self._client.get(
            parts["realm"],
            params={
                "service": parts.get("service", ""),
                "scope": parts.get("scope", ""),
            },
            auth=(self._username, self._password),
        )
        r.raise_for_status()
        return r.json()["token"]

    async def _ensure_token(self) -> None:
        """Trigger an unauthenticated probe to obtain the Bearer challenge."""
        if self._token:
            return
        r = await self._client.get(f"https://{self._registry}/v2/")
        if r.status_code == 401 and "www-authenticate" in r.headers:
            self._token = await self._fetch_token(r.headers["www-authenticate"])

    def _auth_headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Authenticated request; auto-refreshes token on a single 401."""
        headers = {**self._auth_headers(), **kwargs.pop("headers", {})}
        r = await getattr(self._client, method)(url, headers=headers, **kwargs)
        if r.status_code == 401 and "www-authenticate" in r.headers:
            self._token = await self._fetch_token(r.headers["www-authenticate"])
            headers = {**headers, **self._auth_headers()}
            r = await getattr(self._client, method)(url, headers=headers, **kwargs)
        return r

    # ------------------------------------------------------------------
    # OCI primitives
    # ------------------------------------------------------------------

    async def _blob_exists(self, digest: str) -> bool:
        r = await self._request("head", f"{self._base}/blobs/{digest}")
        return r.status_code == 200

    async def _push_blob(self, data: bytes, digest: str) -> None:
        if await self._blob_exists(digest):
            return
        r = await self._request("post", f"{self._base}/blobs/uploads/")
        r.raise_for_status()
        location = r.headers["location"]
        if not location.startswith("http"):
            location = f"https://{self._registry}{location}"
        sep = "&" if "?" in location else "?"
        r = await self._request(
            "put",
            f"{location}{sep}digest={digest}",
            content=data,
            headers={"Content-Type": _MT_BLOB},
        )
        if r.status_code not in (201, 202):
            raise RuntimeError(f"Blob push failed {r.status_code}: {r.text[:200]}")

    async def _put_manifest(self, tag: str, manifest: dict) -> None:
        body = json.dumps(manifest, separators=(",", ":")).encode()
        r = await self._request(
            "put",
            f"{self._base}/manifests/{tag}",
            content=body,
            headers={"Content-Type": _MT_MANIFEST},
        )
        if r.status_code not in (201, 202):
            raise RuntimeError(f"Manifest push failed {r.status_code}: {r.text[:200]}")

    async def _get_manifest(self, tag: str) -> dict | None:
        r = await self._request(
            "get",
            f"{self._base}/manifests/{tag}",
            headers={"Accept": _MT_MANIFEST},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def _get_manifest_digest(self, tag: str) -> str | None:
        """HEAD the manifest; returns the registry-assigned digest or None if absent."""
        r = await self._request(
            "head",
            f"{self._base}/manifests/{tag}",
            headers={"Accept": _MT_MANIFEST},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.headers.get("docker-content-digest") or r.headers.get("content-digest")

    # ------------------------------------------------------------------
    # FileStorageService
    # ------------------------------------------------------------------

    async def upload_file(self, file_data: BytesIO, file_name: str) -> str:
        file_data.seek(0)
        payload = file_data.read()
        blob_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        tag = _tag(file_name)

        # The empty config blob is shared across all artifacts; push is idempotent.
        await self._push_blob(_EMPTY_CONFIG, _EMPTY_CONFIG_DIGEST)
        await self._push_blob(payload, blob_digest)
        await self._put_manifest(tag, {
            "schemaVersion": 2,
            "mediaType": _MT_MANIFEST,
            "config": {
                "mediaType": _MT_CONFIG,
                "digest": _EMPTY_CONFIG_DIGEST,
                "size": len(_EMPTY_CONFIG),
            },
            "layers": [{
                "mediaType": _MT_BLOB,
                "digest": blob_digest,
                "size": len(payload),
                "annotations": {_ANN_PATH: file_name},
            }],
            "annotations": {_ANN_PATH: file_name},
        })
        logger.info("QuayOrasStorage: pushed %s (%d B, tag=%s)", file_name, len(payload), tag)
        return file_name

    async def download_file(self, file_name: str) -> BytesIO:
        tag = _tag(file_name)
        manifest = await self._get_manifest(tag)
        if not manifest:
            raise FileNotFoundError(f"File not found: {file_name}")
        digest = manifest["layers"][0]["digest"]
        r = await self._request("get", f"{self._base}/blobs/{digest}")
        if r.status_code == 404:
            raise FileNotFoundError(f"File not found: {file_name}")
        # Quay redirects blob GETs to its backing object store (e.g. S3 pre-signed URL).
        # Must NOT forward the Bearer token: AWS rejects requests that combine an
        # Authorization header with X-Amz-Signature query params.
        if r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get("location", "")
            r = await self._client.get(location, follow_redirects=True)
        r.raise_for_status()
        logger.info("QuayOrasStorage: pulled %s (%d B)", file_name, len(r.content))
        return BytesIO(r.content)

    async def delete_file(self, file_name: str) -> bool:
        tag = _tag(file_name)
        manifest_digest = await self._get_manifest_digest(tag)
        if not manifest_digest:
            return True  # idempotent
        r = await self._request("delete", f"{self._base}/manifests/{manifest_digest}")
        if r.status_code not in (202, 404):
            raise RuntimeError(f"Manifest delete failed {r.status_code}: {r.text[:200]}")
        logger.info("QuayOrasStorage: deleted %s", file_name)
        return True

    async def exists(self, file_name: str) -> bool:
        digest = await self._get_manifest_digest(_tag(file_name))
        return digest is not None
