"""Names, paths and identifiers of stored files: what a client may upload, how a stored reference maps to a store path."""
import posixpath
import re
from urllib.parse import urlparse, urlsplit
from uuid import UUID

from app.logic.http import split_csv

# Uploads are stored as "<uuid>_<original name>".
_UUID_PREFIX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_", re.IGNORECASE)


def is_supported_extension(file_name: str, allowed_csv: str) -> bool:
    """Whether the file's extension is one of the comma-separated `allowed_csv` (with the leading dot, any case)."""
    allowed = {ext.lower() for ext in split_csv(allowed_csv)}
    ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return ext in allowed


# What the first bytes of a binary format must be. Text-like extensions have no signature, so they are
# only checked for being non-empty. Not a malware scan: it stops a renamed or corrupt file, nothing more.
_SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".docx": (b"PK\x03\x04",),
    ".xlsx": (b"PK\x03\x04",),
    ".pptx": (b"PK\x03\x04",),
    ".png": (b"\x89PNG",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}


def upload_problem(file_name: str, contents: bytes) -> str | None:
    """Why an upload's bytes cannot be what its name says, or None if nothing looks wrong."""
    if not contents:
        return f"{file_name} is empty."
    ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    signatures = _SIGNATURES.get(ext)
    if signatures and not contents.startswith(signatures):
        return f"{file_name} does not look like a valid {ext} file."
    return None


def max_upload_bytes(megabytes: int) -> int:
    return megabytes * 1024 * 1024


def stored_name(upload_id: str, file_name: str) -> str:
    """The key an upload is stored under, unique per upload so two files with one name never collide."""
    return f"{upload_id}_{file_name}"


def download_name(blob_name: str) -> str:
    """The name a browser should save a blob under: no folders, no upload prefix."""
    return _UUID_PREFIX.sub("", posixpath.basename(blob_name))


def extract_blob_path_from_url(url: str) -> str:
    """Reduces a stored file reference to a store-relative path.

    File records hold whatever the storage backend returned from upload_file: an
    absolute https URL under Azure, a relative path under a mounted volume. The
    compute service is given a path, so the URL form is trimmed back to one - the
    leading segment after the host is the container name and is dropped with it.
    A value that is already relative is returned unchanged.

    Only http(s) counts as "already a URL": `urlparse` gives a scheme to any text
    before a colon that looks like one, so a store-relative path such as
    "invoice:2024.pdf" would otherwise lose everything up to the colon.
    """
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url.lstrip("/")
    path = parsed.path.strip("/")
    return path.split("/", 1)[1] if "/" in path else path


def azure_blob_name(file_reference: str, container_name: str) -> str:
    """The raw blob name from either a blob name or a blob URL.

    More conservative than extract_blob_path_from_url: it only strips a leading
    segment equal to this container's own name, so it is a no-op on a value that
    already went through that function.
    """
    parsed = urlsplit(file_reference)
    if not parsed.scheme:
        return file_reference.lstrip("/")
    path = parsed.path.lstrip("/")
    container_prefix = f"{container_name}/"
    return path[len(container_prefix):] if path.startswith(container_prefix) else path


def classify_file_id(file_id: str) -> tuple[str, int | str]:
    """("sql", id) for an integer file-record id, ("kb", uuid) for a knowledge-base document id.

    Raises ValueError for anything else.
    """
    try:
        return "sql", int(file_id)
    except ValueError:
        UUID(file_id)
        return "kb", file_id
