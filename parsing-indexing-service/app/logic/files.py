"""File path utilities for the worker."""
from urllib.parse import urlparse


def extract_blob_path_from_url(url: str) -> str:
    """Reduces a stored file reference to a store-relative path.

    File records hold whatever the storage backend returned from upload_file: an
    absolute https URL under Azure, a relative path under a mounted volume. The
    worker is given a path, so the URL form is trimmed back to one.
    A value that is already relative is returned unchanged.
    """
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url.lstrip("/")
    path = parsed.path.strip("/")
    return path.split("/", 1)[1] if "/" in path else path
