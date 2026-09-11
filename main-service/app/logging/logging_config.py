"""Logging configuration for the FastAPI application.

This module sets up the logging system using a YAML configuration file.
It includes a custom formatter for Uvicorn error logs to provide cleaner output.
"""
import logging
import logging.config
import yaml
import os


# --- Custom Formatter Class ---
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


# --- Configuration Loading Function ---

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
