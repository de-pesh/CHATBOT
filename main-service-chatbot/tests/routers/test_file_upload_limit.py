"""POST /files rejects an over-size upload before reading it into memory when the size is declared."""
import pytest
from app.config import Settings
from app.routers.file import create_files
from fastapi import HTTPException

MB = 1024 * 1024


class FakeUpload:
    def __init__(self, size, content=b"x", filename="a.pdf"):
        self.size = size
        self.filename = filename
        self.content_type = "application/pdf"
        self._content = content
        self.reads = 0

    async def read(self):
        self.reads += 1
        return self._content


def _settings(limit_mb=1):
    return Settings(
        _env_file=None, REDIS_URL="redis://x", CHATBOT_QUEUE_NAME="q", DB_CONNECTION_STRING="x", SECRET_KEY="x",
        MAX_UPLOAD_SIZE_MB=limit_mb,
    )


async def _upload(upload, settings):
    return await create_files(
        current_user=None, files=[upload], db_service=None, file_storage_service=None, chatbot_queue=None,
        settings=settings,
    )


@pytest.mark.asyncio
async def test_a_file_that_declares_a_size_over_the_limit_is_refused_without_being_read():
    upload = FakeUpload(size=2 * MB)

    with pytest.raises(HTTPException) as refused:
        await _upload(upload, _settings(1))

    assert refused.value.status_code == 413 and "MAX_UPLOAD_SIZE_MB" in refused.value.detail
    assert upload.reads == 0


@pytest.mark.asyncio
async def test_a_file_of_unknown_size_is_still_refused_after_it_is_read():
    upload = FakeUpload(size=None, content=b"x" * (2 * MB))

    with pytest.raises(HTTPException) as refused:
        await _upload(upload, _settings(1))

    assert refused.value.status_code == 413
    assert upload.reads == 1


@pytest.mark.asyncio
async def test_a_file_that_declares_less_than_it_sends_is_refused_after_it_is_read():
    upload = FakeUpload(size=10, content=b"x" * (2 * MB))

    with pytest.raises(HTTPException) as refused:
        await _upload(upload, _settings(1))

    assert refused.value.status_code == 413 and upload.reads == 1


def test_the_connection_pool_needs_at_least_one_connection():
    with pytest.raises(ValueError):
        Settings(
            _env_file=None, REDIS_URL="redis://x", CHATBOT_QUEUE_NAME="q", DB_CONNECTION_STRING="x", SECRET_KEY="x",
            MSSQL_POOL_SIZE=0,
        )
