"""Request tracing and logging setup.

Stamps a request ID onto every log line.

`logging.yaml` sits next to this file and names `RequestIDFilter` and
`UvicornErrorLogFormatter` below by their dotted path, so renaming either one
means editing that YAML too.
"""
import logging
import logging.config
import os
import uuid
from collections.abc import Callable
from contextvars import ContextVar

import yaml
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings

# --- Request context -----------------------------------------------------------

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        token = request_id_context.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_context.reset(token)  # a reused task must not inherit this request's id


class RequestIDFilter(logging.Filter):
    """Attaches the current request ID to every record, for the `default` formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        return True


# --- Formatters ----------------------------------------------------------------

class UvicornErrorLogFormatter(logging.Formatter):
    """Drops the logger name from INFO records of `uvicorn.error`, which makes the common server
    startup/shutdown lines cleaner; every other record keeps the full format."""
    default_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    info_format = "%(asctime)s - %(levelname)s - %(message)s"

    def __init__(self, fmt=None, datefmt=None, style='%', validate=True):
        super().__init__(fmt=self.default_format, datefmt=datefmt, style=style)

    def format(self, record):
        original_format = self._style._fmt
        is_uvicorn_info = record.name == "uvicorn.error" and record.levelno == logging.INFO
        self._style._fmt = self.info_format if is_uvicorn_info else self.default_format
        try:
            return logging.Formatter.format(self, record)
        finally:
            self._style._fmt = original_format


# --- Configuration Loading Function --------------------------------------------

DEFAULT_LOGGING_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'logging.yaml')
_FALLBACK_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'


def setup_logging(config_path: str = DEFAULT_LOGGING_CONFIG_PATH):
    """Applies the logging.yaml configuration, falling back to basic console logging if the
    file is missing or invalid."""
    if not os.path.exists(config_path):
        logging.basicConfig(level=logging.INFO, format=_FALLBACK_FORMAT)
        logging.warning(f"Logging config file not found at {config_path}. Falling back to basic config.")
        return
    try:
        with open(config_path) as f:
            logging.config.dictConfig(yaml.safe_load(f.read()))
        logging.info(f"Logging configuration loaded successfully from: {config_path}")
    except Exception:
        logging.basicConfig(level=logging.INFO, format=_FALLBACK_FORMAT)
        logging.exception(f"Error loading logging config from {config_path}. Falling back to basic config.")


# The loggers logging.yaml gives a level of their own besides `app`; LOG_LEVEL_LIBRARIES governs all of them.
# The uvicorn.* loggers are deliberately not here: uvicorn owns their levels.
_LIBRARY_LOGGERS = ("", "azure", "pymongo.client")


def apply_log_levels(settings: Settings) -> None:
    """Sets the log levels from Settings once they are loaded. logging.yaml (applied before Settings can be
    read) holds the same values as defaults: `app` follows LOG_LEVEL, and the root logger plus the noisy
    third-party loggers follow LOG_LEVEL_LIBRARIES."""
    logging.getLogger("app").setLevel(settings.LOG_LEVEL)
    for name in _LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(settings.LOG_LEVEL_LIBRARIES)
