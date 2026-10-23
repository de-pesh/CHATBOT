"""Store-relative paths: what a reference to a stored file means, and where derived files go."""
import os
from urllib.parse import urlsplit


def file_extension(path: str) -> str:
    """Lowercased extension including the dot ('' if none)."""
    return os.path.splitext(os.path.basename(path))[1].lower()


def text_blob_path(source_path: str, unique: str, extension: str = ".txt") -> str:
    """Where the text extracted from `source_path` is stored: a `texts/<unique><extension>`
    under the original's own "directory" (blob storage has no real directories, but
    a "/" in a key is displayed as one). Real upload keys are flat, and dirname gives
    "" for those, where rsplit('/') would return the whole filename and make the file
    its own directory."""
    name = f"texts/{unique}{extension}"
    directory = os.path.dirname(source_path)
    return f"{directory}/{name}" if directory else name


def blob_name(reference: str, container: str) -> str:
    """A container-relative blob name from either a blob URL or a raw path."""
    parsed = urlsplit(reference)
    if not parsed.scheme:
        return reference.lstrip("/")
    path = parsed.path.lstrip("/")
    return path[len(container) + 1:] if path.startswith(f"{container}/") else path


def volume_relative(reference: str) -> str:
    """A path under the local volume from a store path or an old Azure URL.

    File records written against Azure hold a full https URL, and are kept readable
    by taking its path component. Raises ValueError for an empty reference; whether
    the result stays inside the volume is for the caller to check on disk."""
    reference = (reference or "").strip()
    if not reference:
        raise ValueError("file_name must not be empty.")
    parsed = urlsplit(reference)
    if parsed.scheme in ("http", "https"):
        reference = parsed.path
    return reference.replace("\\", "/").lstrip("/")


def parse_dir_mode(text: str) -> int:
    """'770' (or '0770', '0o770') -> 0o770. Raises ValueError unless it is an octal
    permission between 000 and 777."""
    try:
        mode = int(text.strip(), 8)
    except ValueError:
        raise ValueError(f"{text!r} is not an octal permission such as '770' or '755'.") from None
    if not 0 <= mode <= 0o777:
        raise ValueError(f"{text!r} is outside the permission range 000-777.")
    return mode
