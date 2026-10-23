"""LocalFileStorage against a temp directory: round trips, path safety, old Azure URLs."""
import os
import stat
import sys
from io import BytesIO
from pathlib import Path

import pytest
from app.config import Settings
from app.services.storage.local_file_storage import LocalFileStorage
from app.services.storage.storage_factory import create_storage_service


@pytest.fixture
async def store(tmp_path):
    storage = LocalFileStorage(str(tmp_path))
    await storage.initialize()
    return storage


async def test_upload_download_exists_delete_round_trip(store, tmp_path):
    assert await store.upload_file(BytesIO(b"hello"), "kb/a.txt") == "kb/a.txt"
    assert await store.exists("kb/a.txt")
    assert (await store.download_file("kb/a.txt")).read() == b"hello"
    assert not list(tmp_path.rglob("*.partial"))  # staged then moved into place
    assert await store.delete_file("kb/a.txt") and not await store.exists("kb/a.txt")
    assert await store.delete_file("kb/a.txt")  # deleting what is gone succeeds


async def test_upload_text_goes_through_upload_file(store):
    await store.upload_text("t.txt", "héllo")
    assert (await store.download_file("t.txt")).read().decode() == "héllo"


async def test_a_missing_file_is_file_not_found_and_a_directory_is_not_a_file(store):
    with pytest.raises(FileNotFoundError):
        await store.download_file("nope.txt")
    await store.upload_file(BytesIO(b"x"), "dir/a.txt")
    assert not await store.exists("dir")


@pytest.mark.parametrize("reference", ["../escape.txt", "a/../../escape.txt", r"..\escape.txt"])
async def test_paths_that_leave_the_root_are_refused(store, reference):
    with pytest.raises(ValueError, match="outside the store"):
        await store.upload_file(BytesIO(b"x"), reference)
    assert not await store.exists(reference)


async def test_an_empty_reference_is_refused(store):
    with pytest.raises(ValueError, match="must not be empty"):
        await store.download_file("  ")


async def test_old_azure_urls_and_leading_slashes_and_backslashes_resolve_to_the_same_file(store):
    await store.upload_file(BytesIO(b"same"), "kb/a.txt")
    for reference in ("https://acct.blob.core.windows.net/kb/a.txt", "/kb/a.txt", r"kb\a.txt"):
        assert (await store.download_file(reference)).read() == b"same"


@pytest.mark.skipif(sys.platform == "win32", reason="unlink on a directory raises IsADirectoryError only on POSIX")
async def test_deleting_a_directory_removes_it(store, tmp_path):
    await store.upload_file(BytesIO(b"x"), "dir/a.txt")
    assert await store.delete_file("dir")
    assert not (tmp_path / "dir").exists()


def test_a_root_is_required():
    with pytest.raises(ValueError):
        LocalFileStorage("")


async def test_every_directory_level_the_store_creates_gets_the_configured_mode(tmp_path, monkeypatch):
    storage = LocalFileStorage(str(tmp_path), directory_mode=0o750)
    await storage.initialize()
    (tmp_path / "old").mkdir()
    changed = []
    real_chmod = Path.chmod
    monkeypatch.setattr(Path, "chmod", lambda self, mode: (changed.append((self, mode)), real_chmod(self, mode))[1])
    await storage.upload_file(BytesIO(b"x"), "old/new/deeper/a.txt")
    # Only the two levels that did not exist, outermost first - not the existing one.
    assert changed == [(tmp_path / "old" / "new", 0o750), (tmp_path / "old" / "new" / "deeper", 0o750)]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX permission bits")
async def test_the_mode_survives_a_restrictive_umask(tmp_path):
    storage = LocalFileStorage(str(tmp_path), directory_mode=0o770)
    await storage.initialize()
    previous = os.umask(0o077)
    try:
        await storage.upload_file(BytesIO(b"x"), "a/b/c.txt")
    finally:
        os.umask(previous)
    assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o770
    assert stat.S_IMODE((tmp_path / "a" / "b").stat().st_mode) == 0o770


def test_the_factory_hands_the_configured_mode_to_the_store(tmp_path):
    settings = Settings(
        _env_file=None, STORAGE_TYPE="local", LOCAL_STORAGE_PATH=str(tmp_path), LOCAL_STORAGE_DIR_MODE="755"
    )
    assert create_storage_service(settings)._directory_mode == 0o755
    assert create_storage_service(Settings(
        _env_file=None, STORAGE_TYPE="local", LOCAL_STORAGE_PATH=str(tmp_path)
    ))._directory_mode == 0o770
