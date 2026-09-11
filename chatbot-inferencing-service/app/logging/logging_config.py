import logging
import logging.config
import yaml
import os


# --- Custom Formatter Class ---
class UvicornErrorLogFormatter(logging.Formatter):
    """
    Custom formatter to adjust log format for Uvicorn's operational messages.
    Removes logger name for INFO level messages from uvicorn.error logger.
    """
    # Define the two formats
    default_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    info_format = "%(asctime)s - %(levelname)s - %(message)s"

    def __init__(self, fmt=None, datefmt=None, style='%', validate=True):
        # Initialize with default_format, but we'll override in format()
        super().__init__(fmt=self.default_format, datefmt=datefmt, style=style)

    def format(self, record):
        # Save the original format config
        original_format = self._style._fmt
        original_name = record.name # Store original logger name if needed elsewhere

        # Change format string based on level for the uvicorn.error logger
        if record.name == "uvicorn.error" and record.levelno == logging.INFO:
            self._style._fmt = self.info_format
        else:
            self._style._fmt = self.default_format

        # Call the original formatter class to do the grunt work
        result = logging.Formatter.format(self, record)

        # Restore the original format config
        self._style._fmt = original_format

        return result


# --- Configuration Loading Function ---

DEFAULT_LOGGING_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'logging.yaml')

def setup_logging(config_path: str = DEFAULT_LOGGING_CONFIG_PATH):
    """
    Loads logging configuration from a YAML file and applies it.
    Falls back to basic console logging if the file is not found or invalid.
    """
    path = config_path
    if os.path.exists(path):
        try:
            with open(path, 'rt') as f:
                config_data = yaml.safe_load(f.read())
            logging.config.dictConfig(config_data)
            logging.info(f"Logging configuration loaded successfully from: {path}")
        except Exception as e:
            logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
            logging.exception(f"Error loading logging config from {path}. Falling back to basic config.", exc_info=True)
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        logging.warning(f"Logging config file not found at {path}. Falling back to basic config.")