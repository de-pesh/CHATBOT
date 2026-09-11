import logging
from fastapi import Request
from app.services.database_service import DatabaseService


logger = logging.getLogger(__name__)

def get_db_service(request: Request) -> DatabaseService:
    """Dependency that provides the database service instance from the application state."""
    return request.app.state.db_service

