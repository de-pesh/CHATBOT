from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.models.error_models import ErrorMessage, ErrorResponse


async def http_exception_handler(request: Request, exc: HTTPException):
    '''
    Handles HTTP exceptions globally.

    Parameters:
        request (Request): The incoming request instance.
        exc (HTTPException): The raised HTTP exception.

    Returns:
        JSONResponse: A JSON response containing the error message and status code.
    '''
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            message=ErrorMessage(error=exc.detail)
        ).model_dump(mode="json")
    )

async def generic_exception_handler(request: Request, exc: Exception):
    '''
    Handles generic exceptions globally.

    Parameters:
        request (Request): The incoming request instance.
        exc (Exception): The raised exception.

    Returns:
        JSONResponse: A JSON response with a 500 Internal Server Error status.
    '''
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            message=ErrorMessage(error=str(exc))
        ).model_dump(mode="json")
    )
