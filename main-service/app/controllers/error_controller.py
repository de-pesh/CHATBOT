import logging
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from app.models.common import ErrorResponse, BaseAppException

# Get a logger instance
logger = logging.getLogger(__name__)


async def app_exception_handler(request: Request, exc: BaseAppException):
    """
    Handles custom application exceptions derived from BaseAppException.

    Uses the status_code and detail provided by the specific exception instance.
    Logs the error appropriately based on its severity (implied by status code).
    """
    log_level = logging.ERROR if exc.status_code >= 500 else logging.WARNING
    logger.log(
        log_level,
        f"Application error occurred: {exc.detail} "
        f"(Handler: app_exception_handler, Path: {request.url.path}, Method: {request.method})",
        exc_info=False
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(detail=exc.detail).model_dump(mode="json"),
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Handles FastAPI's built-in HTTPException.

    Uses the status_code and detail from the HTTPException.
    Logs the error, typically as a warning for 4xx errors and error for 5xx.
    """
    log_level = logging.ERROR if exc.status_code >= 500 else logging.WARNING
    logger.log(
        log_level,
        f"HTTPException occurred: {exc.detail} "
        f"(Handler: http_exception_handler, Path: {request.url.path}, Method: {request.method})",
        exc_info=False
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(detail=exc.detail).model_dump(mode="json"),
    )


async def generic_exception_handler(request: Request, exc: Exception):
    """
    Handles any uncaught exception (catch-all).

    Logs the full exception traceback as an error.
    Returns a generic 500 Internal Server Error response to the client
    to avoid leaking sensitive internal details.
    """
    logger.exception(
        f"Unhandled exception occurred: {exc} "
        f"(Handler: generic_exception_handler, Path: {request.url.path}, Method: {request.method})",
    )

    # Return a generic error message to the client
    generic_detail = "An unexpected internal server error occurred."
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(detail=generic_detail).model_dump(mode="json"),
    )
