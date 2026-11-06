"""The FastAPI application: logging, middleware, exception handlers, routers and the health endpoints."""
import logging

from fastapi import FastAPI, Request, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware

from app.config import get_settings
from app.controllers.error_controller import (
    generic_exception_handler,
    http_exception_handler,
)
from app.lifespan_manager import lifespan
from app.logic.http import cors_origins
from app.models.common import StatusResponse
from app.observability import RequestContextMiddleware, setup_logging
from app.routers import admin, auth, chat, dms, file, internal, prompts

setup_logging()
logger = logging.getLogger("app")

# Read here, not only in the lifespan, because the middleware below is wired in at app construction.
_settings = get_settings()

app = FastAPI(
    title="Chatbot Main Service",
    version="1.0.0",
    lifespan=lifespan,
    middleware=[
        Middleware(RequestContextMiddleware),
        Middleware(GZipMiddleware, minimum_size=_settings.GZIP_MIN_SIZE_BYTES),
    ],
)

# CORS_ALLOWED_ORIGINS adds to CORS_LOCAL_ORIGINS rather than replacing it, so the local-dev
# origins keep working with no .env changes; set it to whatever origin actually calls this API
# in a real deployment (e.g. the Module Federation host app's OpenShift route).
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(_settings.CORS_LOCAL_ORIGINS, _settings.CORS_ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(StarletteHTTPException, http_exception_handler)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation error: {exc.errors()} for request: {request.url}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        # jsonable_encoder: the error of a custom validator carries the exception it raised, which
        # json cannot write - without it a rejected body is a 500 instead of this 422.
        content={"detail": "Validation Error", "errors": jsonable_encoder(exc.errors())},
    )


app.add_exception_handler(Exception, generic_exception_handler)  # the catch-all: must be registered last

app.include_router(file.router, prefix="/files", tags=["Files"])
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])
app.include_router(prompts.router, prefix="/prompts", tags=["Prompt Management"])
app.include_router(chat.router)  # declares its own prefix="/chat", unlike the others
app.include_router(dms.router)   # declares its own prefix="/dms"
app.include_router(internal.router, prefix="/internal", include_in_schema=False)


def _build_openapi_schema():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema.setdefault("components", {}).setdefault("securitySchemes", {}).update({
        "UserId":        {"type": "apiKey", "in": "header", "name": "x-user-id"},
        "EntityId":      {"type": "apiKey", "in": "header", "name": "x-entity-id"},
        "TenantId":      {"type": "apiKey", "in": "header", "name": "x-tenant-id"},
        "UserType":      {"type": "apiKey", "in": "header", "name": "x-user-type"},
        "InternalToken": {"type": "apiKey", "in": "header", "name": "x-internal-token"},
    })
    schema["security"] = [{"UserId": [], "EntityId": [], "TenantId": [], "UserType": [], "InternalToken": []}]
    app.openapi_schema = schema
    return schema


app.openapi = _build_openapi_schema  # type: ignore[method-assign]


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="Chatbot Main Service",
        swagger_ui_parameters={"persistAuthorization": True},
    )


@app.get(
    path="/",
    tags=["Monitoring"],
    response_model=StatusResponse,
    summary="Check Service Status",
    description="A simple endpoint to check if the service is running.",
)
def root() -> StatusResponse:
    return StatusResponse(status="Service is running...")


@app.get(
    path="/health",
    tags=["Monitoring"],
    response_model=StatusResponse,
    summary="Health Check",
    description="A health check endpoint to verify the service is running.",
)
def health_check() -> StatusResponse:
    return StatusResponse(status="healthy")
