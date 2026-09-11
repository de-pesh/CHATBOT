"""
Main application file for the CCPM Main Service.

This module initializes the FastAPI application, sets up logging, registers
global exception handlers, includes API routers, and defines basic health
check endpoints.
"""
import logging
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware

# --- Import Logging Setup ---
from app.logging.logging_config import setup_logging
from app.models.common import StatusResponse
from app.routers.v1 import (
    auth,
    permission,
    role_permission,
    role,
    user_role_assignment_permission,
    user,
    organization,
    framework,
    file,
    kb
)

# --- Call Logging Setup EARLY ---
# Load configuration from app/logging.yaml before doing anything else
setup_logging()

# --- Now get the logger for this module AFTER setup ---
logger = logging.getLogger("app")

# --- Custom Error Handling Imports ---
from app.controllers.error_controller import (
    app_exception_handler,
    generic_exception_handler,
    http_exception_handler
)
from app.models.common import BaseAppException

# --- Custom Middleware Imports ---
from app.middleware.request_context import RequestContextMiddleware

# --- Configuration ---
from app.lifespan_manager import lifespan


# Initialize the FastAPI application
# Configures application metadata, lifespan events, and global middleware.
app = FastAPI(
    title="CCPM Main Service",
    version="1.0.0",
    lifespan=lifespan, # Register the lifespan handler for startup/shutdown events
    middleware=[
        Middleware(RequestContextMiddleware), # Custom middleware for managing request context
        Middleware(GZipMiddleware, minimum_size=102400) # GZip compression for responses larger than 100KB
    ]
)

#App MIddleware
origins = [    
    "http://localhost:3000",
    "http://*:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# Register global exception handlers
# These handlers catch and process exceptions that occur during request processing,
# providing consistent error responses.
logger.info("Registering global exception handlers...")
app.add_exception_handler(BaseAppException, app_exception_handler) # Handles custom application-specific exceptions
app.add_exception_handler(StarletteHTTPException, http_exception_handler) # Handles standard FastAPI/Starlette HTTP exceptions

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation error: {exc.errors()} for request: {request.url}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Validation Error",
            "errors": exc.errors()
        },
    )
app.add_exception_handler(Exception, generic_exception_handler) # Generic fallback handler for all other unhandled exceptions (MUST be last)
logger.info("Exception handlers registered.")


# Include API routers for different functionalities
# Each router groups related endpoints, improving modularity and organization.
logger.info("Including API routers...")
app.include_router(organization.router, prefix="/organizations", tags=["Organizations"])
app.include_router(framework.router, prefix="/frameworks", tags=["Frameworks"])
app.include_router(file.router, prefix="/files", tags=["Files"])
app.include_router(kb.router, prefix="/kb", tags=["Knowledge Base"])
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(permission.router, prefix="/permissions", tags=["Permissions"])
app.include_router(role_permission.router, prefix="/role-permissions", tags=["Role Permissions"])
app.include_router(role.router, prefix="/roles", tags=["Roles"])
app.include_router(user_role_assignment_permission.router, prefix="/user-role-assignments", tags=["User Role Assignments"])
app.include_router(user.router, prefix="/user", tags=["User"])


logger.info("API routers included.")


# Define the root endpoint
@app.get(
        path="/",
        tags=["Monitoring"],
        response_model=StatusResponse,
        summary="Check Service Status",
        description="A simple endpoint to check if the service is running."
)
def root() -> StatusResponse:
    """Root endpoint that checks if the service is running.

    Returns:
        StatusResponse: A response indicating the service status.
    """
    logger.debug("Root endpoint '/' called.")
    return StatusResponse(status="Service is running...")


# Define the health check endpoint
@app.get(
        path="/health",
        tags=["Monitoring"],
        response_model=StatusResponse,
        summary="Health Check",
        description="A health check endpoint to verify the service is running."
)
def health_check() -> StatusResponse:
    """Health check endpoint to verify the service is running.

    Returns:
        StatusResponse: A response indicating the service health.
    """
    logger.debug("Health check endpoint called.")
    return StatusResponse(status="healthy")