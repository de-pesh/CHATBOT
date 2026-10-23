"""Hashing the files uploaded before duplicate detection existed."""
import hashlib
from io import BytesIO

import pytest
from app.controllers.hash_backfill import backfill_content_hashes
from app.services.db.stored_procedures import StoredProcedures as SP


class FakeDb:
    def __init__(self, rows):
        self.rows, self.hashes = rows, {}

    async def call_procedure(self, name, params=None):
        if name == SP.LIST_FILES_MISSING_HASH:
            return [self.rows]
        if name == SP.SET_FILE_CONTENT_HASH:
            self.hashes[params[0]] = params[1]
            return [[]]
        raise AssertionError(name)


class FakeStorage:
    def __init__(self, files):
        self.files, self.asked = files, []

    async def download_file(self, path):
        self.asked.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return BytesIO(self.files[path])


@pytest.mark.asyncio
async def test_each_file_without_a_hash_is_hashed_from_its_stored_bytes():
    db = FakeDb([{"id": 1, "name": "a.pdf", "file_path": "u/1_a.pdf"}, {"id": 2, "name": "b.pdf", "file_path": "u/2_b.pdf"}])
    storage = FakeStorage({"u/1_a.pdf": b"alpha", "u/2_b.pdf": b"beta"})

    report = await backfill_content_hashes(db, storage)

    assert report == {"checked": 2, "hashed": 2, "unreadable": []}
    assert db.hashes == {1: hashlib.sha256(b"alpha").hexdigest(), 2: hashlib.sha256(b"beta").hexdigest()}


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_read_is_listed_and_the_rest_are_still_hashed():
    db = FakeDb([{"id": 1, "name": "gone.pdf", "file_path": "u/gone.pdf"}, {"id": 2, "name": "b.pdf", "file_path": "u/2_b.pdf"}])

    report = await backfill_content_hashes(db, FakeStorage({"u/2_b.pdf": b"beta"}))

    assert report == {"checked": 2, "hashed": 1, "unreadable": [{"id": 1, "name": "gone.pdf"}]}
    assert list(db.hashes) == [2]


@pytest.mark.asyncio
async def test_nothing_to_do_is_a_clean_report():
    assert await backfill_content_hashes(FakeDb([]), FakeStorage({})) == {"checked": 0, "hashed": 0, "unreadable": []}
