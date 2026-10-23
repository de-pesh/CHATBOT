"""File controller: only the operations the worker needs."""
import logging

from fastapi import HTTPException, status

from app.models.files import FileInDB, ProcessingStatus
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures

logger = logging.getLogger(__name__)


class FileController:
    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    async def get_file_by_id(self, file_id: int) -> FileInDB:
        results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_FILES, [file_id, None, None, None, 1, 1]
        )
        if not results or not results[0]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File record not found.")
        return FileInDB(**results[0][0])

    async def update_processing_status(
        self,
        file_id: int,
        processing_status: ProcessingStatus,
        processing_error: str | None = None,
    ) -> None:
        await self.db_service.call_procedure(
            StoredProcedures.UPDATE_FILE_PROCESSING_STATUS,
            [file_id, processing_status.value, processing_error],
        )
