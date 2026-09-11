"""Common models.

Merged from: generic_models, error_models.
"""
from fastapi import FastAPI, status
from pydantic import BaseModel, Field
from typing import Optional
import aiohttp
import asyncio
import logging


# --- from generic_models.py ---
class StatusResponse(BaseModel):
    status: str = Field(..., description="The status message.")


# --- from error_models.py ---
class ErrorResponse(BaseModel):
    '''
    Represents a standardized error response model.

    Attributes:
        detail (str): A descriptive message detailing the error.
        code (Optional[str]): An optional, programmatic error code.
    '''
    detail: str = Field(..., description="A descriptive message detailing the error.")
    code: Optional[str] = Field(None, description="An optional, programmatic error code.")


# --- Custom Application Exceptions ---

class BaseAppException(Exception):
    """
    Base class for custom application exceptions.
    Allows associating a default HTTP status code, detail message, and an optional error code.
    """
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail: str = "An unexpected internal server error occurred."
    code: Optional[str] = None
    log_traceback: bool = True # New: Control whether traceback is logged for this exception

    def __init__(
        self,
        detail: Optional[str] = None,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        log_traceback: Optional[bool] = None,
    ):
        self.detail = detail or self.detail
        self.status_code = status_code or self.status_code
        self.code = code or self.code
        self.log_traceback = log_traceback if log_traceback is not None else self.log_traceback
        super().__init__(self.detail)


class SessionNotFoundException(Exception):
    """
    A class to raise exception of Session not found.
    """
    # Custom exception for when a session is not found.
    def __init__(
        self,
        token: str
    ):
        super().__init__(f"Session for token: {token} not found")


class AsyncDBLogHandler(logging.Handler):
    # Custom logging handler to send logs to a database asynchronously.
    def __init__(self, app: FastAPI, db_service_url: str):
        super().__init__()
        self.app = app
        self.db_service_url = db_service_url

    def emit(self, record: logging.LogRecord):
        assessment_id = getattr(record, 'assessment_id', None)
        request_id = getattr(record, 'request_id', None) # Get request_id

        # Check 1: Log level must be sufficient for this handler.
        # Check 2: assessment_id must be present.
        if record.levelno >= self.level and assessment_id:
            try:
                # In FastAPI, an event loop is typically running in request context.
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.send_log(record, assessment_id, request_id)) # Pass request_id
                else:
                    # This case is less likely within FastAPI request cycle
                    print(f"AsyncDBLogHandler: No running event loop for {record.msg}. Log for {assessment_id} not sent to DB.")
            except RuntimeError:
                # Handles "no event loop" if called from a context without one
                print(f"AsyncDBLogHandler: RuntimeError getting event loop for {record.msg}. Log for {assessment_id} not sent to DB.")

    async def send_log(self, record: logging.LogRecord, assessment_id: str, request_id: Optional[str]):
        # Sends the log record to the database.
        try:
            log_entry = self.format(record) # Apply formatter attached to this handler

            if not hasattr(self.app.state, 'http_client') or not self.app.state.http_client:
                print(f"AsyncDBLogHandler: app.state.http_client not available. Cannot send log for {assessment_id}.")
                return
            if self.app.state.http_client.closed:
                # This might happen if logs are emitted during shutdown after client is closed
                print(f"AsyncDBLogHandler: app.state.http_client is closed. Cannot send log for {assessment_id}.")
                return

            session: aiohttp.ClientSession = self.app.state.http_client
            url = f"{self.db_service_url}/assessment/{assessment_id}/logs"

            payload = {"service": "main_service", "message": log_entry}
            if request_id:
                payload["request_id"] = request_id

            async with session.patch(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status >= 400:
                    print(f"AsyncDBLogHandler: Failed to log to DB (status {response.status}) for assessment {assessment_id}. URL: {url}. Response: {await response.text()}")
                else:
                    print(f"AsyncDBLogHandler: Successfully logged to DB for assessment {assessment_id}") # For debugging
        except aiohttp.ClientConnectorError as e:
            print(f"AsyncDBLogHandler: Connection error for DB logging for assessment {assessment_id}: {e}")
        except asyncio.TimeoutError:
            print(f"AsyncDBLogHandler: Timeout while logging to DB for assessment {assessment_id}")
        except Exception as e:
            # Catch-all for other unexpected errors during log sending
            print(f"AsyncDBLogHandler: CRITICAL - Unexpected error in send_log for assessment {assessment_id}: {e} (Log Record: {record.msg})")
