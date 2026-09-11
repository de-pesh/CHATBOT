"""Request tracing and logging setup.

Consolidates what used to be three modules across two packages:
`middleware/request_context.py`, `logging/filters.py` and
`logging/logging_config.py`. They were always a single concern - stamping a
request ID onto every log line - and the old `app.logging` package name
shadowed the stdlib `logging` module for anyone reading the imports.

`logging.yaml` sits next to this file and names `RequestIDFilter` and
`UvicornErrorLogFormatter` below by their dotted path, so renaming either one
means editing that YAML too.
"""
import logging
import logging.config
import os
import uuid
from contextvars import ContextVar
from typing import Callable

import yaml
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# --- Request context -----------------------------------------------------------

# Holds the current request's ID for the duration of one async task.
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        # Set the request ID in the context variable for the current async task
        token = request_id_context.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id # Optionally add to response headers
            return response
        finally:
            # Reset the context variable to its previous state (important for task reuse)
            request_id_context.reset(token)


class RequestIDFilter(logging.Filter):
    """Attaches the current request ID to every record, for the `default` formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        return True


# --- Formatters ----------------------------------------------------------------

class UvicornErrorLogFormatter(logging.Formatter):
    """
    Custom formatter to adjust log format for Uvicorn's operational messages.

    Specifically, it removes the logger name for INFO level messages originating
    from the `uvicorn.error` logger, making the output cleaner for common
    server startup/shutdown messages.
    """
    # Define the two formats: one for general logs, one for specific Uvicorn INFO logs.
    default_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    info_format = "%(asctime)s - %(levelname)s - %(message)s"

    def __init__(self, fmt=None, datefmt=None, style='%', validate=True):
        """Initializes the custom formatter.

        Args:
            fmt (str, optional): The format string. Defaults to None.
            datefmt (str, optional): The date format string. Defaults to None.
            style (str, optional): The style of the format string ('%', '{', '$'). Defaults to '%'.
            validate (bool, optional): Whether to validate the format string. Defaults to True.
        """
        # Initialize with default_format; the format method will dynamically change it.
        super().__init__(fmt=self.default_format, datefmt=datefmt, style=style)

    def format(self, record):
        """Formats the specified log record as text.

        Overrides the base class method to apply custom formatting rules.

        Args:
            record (logging.LogRecord): The log record to format.

        Returns:
            str: The formatted log record.
        """
        # Save the original format configuration to restore it later.
        original_format = self._style._fmt

        # Apply a different format if the log is from 'uvicorn.error' and is INFO level.
        if record.name == "uvicorn.error" and record.levelno == logging.INFO:
            self._style._fmt = self.info_format
        else:
            # Otherwise, use the default format.
            self._style._fmt = self.default_format

        # Call the original formatter class to perform the actual formatting.
        result = logging.Formatter.format(self, record)

        # Restore the original format configuration to avoid side effects on other loggers.
        self._style._fmt = original_format

        return result


# --- Configuration Loading Function --------------------------------------------

# Define the default path to the logging configuration YAML file.
DEFAULT_LOGGING_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'logging.yaml')


def setup_logging(config_path: str = DEFAULT_LOGGING_CONFIG_PATH):
    """
    Loads logging configuration from a YAML file and applies it.

    If the specified configuration file is found and valid, it will be used to
    configure the logging system. If the file is not found or an error occurs
    during loading, it falls back to a basic console logging setup.

    Args:
        config_path (str): The path to the logging configuration YAML file.
                           Defaults to `DEFAULT_LOGGING_CONFIG_PATH`.
    """
    path = config_path
    if os.path.exists(path):
        try:
            # Open and load the YAML configuration file.
            with open(path, 'rt') as f:
                config_data = yaml.safe_load(f.read())
            # Apply the loaded dictionary configuration to the logging system.
            logging.config.dictConfig(config_data)
            logging.info(f"Logging configuration loaded successfully from: {path}")
        except Exception as e:
            # If an error occurs during YAML loading or dictConfig, fall back to basic logging.
            logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
            logging.exception(f"Error loading logging config from {path}. Falling back to basic config.", exc_info=True)
    else:
        # If the configuration file does not exist, fall back to basic logging.
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logging.warning(f"Logging config file not found at {path}. Falling back to basic config.")
