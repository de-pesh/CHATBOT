'''
Module for defining error models and exception handlers.

This module provides standardized error response models and global exception 
handlers for FastAPI applications. It ensures consistent error handling 
throughout the application.
'''

import asyncio
import logging
import aiohttp
from fastapi import FastAPI
from pydantic import BaseModel

class ErrorMessage(BaseModel):
    '''
    Represents an error message in API responses.

    Attributes:
        error (str): A descriptive message detailing the error.
    '''
    error: str


class ErrorResponse(BaseModel):
    '''
    Represents a standardized error response model.

    Attributes:
        message (ErrorMessage): Contains details about the error.
    '''
    message: ErrorMessage


class SessionNotFoundException(Exception):
    """
    A class to raise exception of Session not found.
    """
    def __init__(
        self,
        token: str
    ):
        super().__init__(f"Session for token: {token} not found")


class AsyncDBLogHandler(logging.Handler):
    def __init__(self, app: FastAPI, db_service_url: str):
        super().__init__()
        self.app = app
        self.db_service_url = db_service_url

    def emit(self, record: logging.LogRecord):
        analysis_id = getattr(record, 'analysis_id', None)

        # Check 1: Log level must be sufficient for this handler.
        # Check 2: analysis_id must be present.
        if record.levelno >= self.level and analysis_id:
            try:
                # In FastAPI, an event loop is typically running in request context.
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.send_log(record, analysis_id))
                else:
                    # This case is less likely within FastAPI request cycle
                    print(f"AsyncDBLogHandler: No running event loop for {record.msg}. Log for {analysis_id} not sent to DB.")
            except RuntimeError:
                # Handles "no event loop" if called from a context without one
                print(f"AsyncDBLogHandler: RuntimeError getting event loop for {record.msg}. Log for {analysis_id} not sent to DB.")

    async def send_log(self, record: logging.LogRecord, analysis_id: str):
        try:
            log_entry = self.format(record) # Apply formatter attached to this handler

            if not hasattr(self.app.state, 'http_client') or not self.app.state.http_client:
                print(f"AsyncDBLogHandler: app.state.http_client not available. Cannot send log for {analysis_id}.")
                return
            if self.app.state.http_client.closed:
                # This might happen if logs are emitted during shutdown after client is closed
                print(f"AsyncDBLogHandler: app.state.http_client is closed. Cannot send log for {analysis_id}.")
                return

            session: aiohttp.ClientSession = self.app.state.http_client
            url = f"{self.db_service_url}/analysis/{analysis_id}/logs"

            async with session.patch(url, json={"service": "main_service", "message": log_entry}, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status >= 400:
                    print(f"AsyncDBLogHandler: Failed to log to DB (status {response.status}) for analysis {analysis_id}. URL: {url}. Response: {await response.text()}")
                else:
                    print(f"AsyncDBLogHandler: Successfully logged to DB for analysis {analysis_id}") # For debugging
        except aiohttp.ClientConnectorError as e:
            print(f"AsyncDBLogHandler: Connection error for DB logging for analysis {analysis_id}: {e}")
        except asyncio.TimeoutError:
            print(f"AsyncDBLogHandler: Timeout while logging to DB for analysis {analysis_id}")
        except Exception as e:
            # Catch-all for other unexpected errors during log sending
            print(f"AsyncDBLogHandler: CRITICAL - Unexpected error in send_log for analysis {analysis_id}: {e} (Log Record: {record.msg})")
