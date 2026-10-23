"""LOG_LEVEL and LOG_LEVEL_LIBRARIES reach the loggers once settings are loaded."""
import logging

import pytest
from app.config import Settings
from app.observability import apply_log_levels
from pydantic import ValidationError

TOUCHED = ("app", "", "azure", "pymongo.client", "uvicorn.error", "uvicorn.access")


def _settings(**overrides):
    return Settings(
        _env_file=None, REDIS_URL="redis://x", CHATBOT_QUEUE_NAME="q", DB_CONNECTION_STRING="x", SECRET_KEY="x",
        **overrides,
    )


@pytest.fixture(autouse=True)
def _restore_levels():
    before = {name: logging.getLogger(name).level for name in TOUCHED}
    yield
    for name, level in before.items():
        logging.getLogger(name).setLevel(level)


def test_the_defaults_are_the_levels_logging_yaml_ships():
    settings = _settings()
    assert (settings.LOG_LEVEL, settings.LOG_LEVEL_LIBRARIES) == ("INFO", "WARNING")


def test_levels_are_applied_to_the_app_and_library_loggers_only():
    uvicorn_levels = {name: logging.getLogger(name).level for name in ("uvicorn.error", "uvicorn.access")}

    apply_log_levels(_settings(LOG_LEVEL="debug", LOG_LEVEL_LIBRARIES="error"))

    assert logging.getLogger("app").level == logging.DEBUG
    assert logging.getLogger("app.services.queue").getEffectiveLevel() == logging.DEBUG
    for name in ("", "azure", "pymongo.client"):
        assert logging.getLogger(name).level == logging.ERROR
    assert {name: logging.getLogger(name).level for name in uvicorn_levels} == uvicorn_levels


def test_a_level_is_case_insensitive_and_normalised():
    assert _settings(LOG_LEVEL=" warning ").LOG_LEVEL == "WARNING"


def test_an_unknown_level_is_rejected():
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        _settings(LOG_LEVEL="chatty")
    with pytest.raises(ValidationError, match="LOG_LEVEL_LIBRARIES"):
        _settings(LOG_LEVEL_LIBRARIES="TRACE")


def test_greeting_hours_must_ascend():
    with pytest.raises(ValidationError, match="ascending"):
        _settings(GREETING_EVENING_START_HOUR=11)
