"""POST /files: refuses byte-identical uploads, remembers each file's hash, and audits what it did."""
import hashlib
from datetime import datetime

import pytest
from app.config import Settings
from app.routers.file import create_files
from app.services.db.stored_procedures import StoredProcedures as SP
from fastapi import HTTPException

CONTENT = b"%PDF-1.7 the same bytes"
HASH = hashlib.sha256(CONTENT).hexdigest()


class FakeUpload:
    filename, content_type, size = "policy.pdf", "application/pdf", len(CONTENT)

    async def read(self):
        return CONTENT


class FakeDb:
    def __init__(self, existing=None):
        self.existing, self.calls = existing, []

    async def call_procedure(self, name, params=None):
        self.calls.append((name, params))
        if name == SP.FIND_FILE_BY_CONTENT_HASH:
            return [[self.existing] if self.existing else []]
        if name == SP.ASSIGN_FILE_VERSION:
            return [[{"version_number": 2}]]
        if name == SP.UPSERT_FILE:
            return [[{"id": 7}]]
        if name == SP.FILTER_FILES:
            return [[{
                "id": 7, "name": "policy.pdf", "mimetype": "application/pdf", "file_path": "u/p.pdf",
                "created_on": datetime(2026, 1, 1), "created_by": "a@x.com",
            }]]
        return [[]]

    def of(self, name):
        return [params for called, params in self.calls if called == name]


class FakeStorage:
    def __init__(self):
        self.uploads = 0

    async def upload_file(self, file_data, file_name):
        self.uploads += 1
        return f"u/{file_name}"


class FakeQueue:
    def __init__(self):
        self.sent = []

    async def send_message(self, payload):
        self.sent.append(payload)


def _settings():
    return Settings(_env_file=None, REDIS_URL="redis://x", CHATBOT_QUEUE_NAME="q", DB_CONNECTION_STRING="x", SECRET_KEY="x")


async def _upload(db, allow_duplicate=False):
    storage, queue = FakeStorage(), FakeQueue()
    user = type("U", (), {"email": "a@x.com"})()
    result = await create_files(
        current_user=user, files=[FakeUpload()], allow_duplicate=allow_duplicate, version_label=None,
        document_key=None, effective_date=None, db_service=db,
        file_storage_service=storage, chatbot_queue=queue, settings=_settings(),
    )
    return result, storage, queue


@pytest.mark.asyncio
async def test_a_byte_identical_upload_is_refused_naming_the_existing_file_and_audited():
    db = FakeDb(existing={"id": 3, "name": "old-policy.pdf"})
    with pytest.raises(HTTPException) as refused:
        await _upload(db)
    assert refused.value.status_code == 409 and "old-policy.pdf" in refused.value.detail
    assert db.of(SP.INSERT_AUDIT_LOG)[0][1:4] == ["file_duplicate", "policy.pdf", "refused"]
    assert db.of(SP.UPSERT_FILE) == []  # nothing was stored


@pytest.mark.asyncio
async def test_an_upload_stores_the_hash_queues_the_job_and_is_audited():
    db = FakeDb()
    result, storage, queue = await _upload(db)
    assert result[0].id == 7 and storage.uploads == 1 and len(queue.sent) == 1
    assert db.of(SP.SET_FILE_CONTENT_HASH) == [[7, HASH]]
    audit = db.of(SP.INSERT_AUDIT_LOG)[0]
    assert audit[:3] == ["a@x.com", "file_upload", "7:policy.pdf"] and HASH in audit[4]


@pytest.mark.asyncio
async def test_allow_duplicate_skips_the_check():
    db = FakeDb(existing={"id": 3, "name": "old-policy.pdf"})
    result, storage, _ = await _upload(db, allow_duplicate=True)
    assert result[0].id == 7 and db.of(SP.FIND_FILE_BY_CONTENT_HASH) == []


@pytest.mark.asyncio
async def test_a_failing_duplicate_lookup_does_not_block_the_upload():
    class Broken(FakeDb):
        async def call_procedure(self, name, params=None):
            if name == SP.FIND_FILE_BY_CONTENT_HASH:
                raise RuntimeError("db hiccup")
            return await super().call_procedure(name, params)

    result, storage, _ = await _upload(Broken())
    assert result[0].id == 7 and storage.uploads == 1


@pytest.mark.asyncio
async def test_an_upload_is_stamped_with_its_version_and_the_job_carries_it():
    db = FakeDb()
    _, _, queue = await _upload(db)

    assert db.of(SP.ASSIGN_FILE_VERSION) == [[7, "policy", None, None]]
    assert queue.sent[0]["document_key"] == "policy" and queue.sent[0]["version_no"] == 2
    assert db.of(SP.INSERT_AUDIT_LOG)[0][4].count('"version_no": 2') == 1
