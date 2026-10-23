"""The tunables that used to be hardcoded keep the values they had, and the extension table matches the enum it replaced."""
import pytest
from app.config import Settings
from app.logic.embeddings import parse_status_codes
from app.parsing.handler import EXTRACTORS


def test_former_constants_default_to_their_old_values():
    settings = Settings(_env_file=None)
    assert settings.EMBEDDING_RETRY_BACKOFF_BASE == 2.0
    assert parse_status_codes(settings.EMBEDDING_RETRY_STATUS_CODES) == {429, 500, 502, 503, 504}
    assert settings.VECTOR_INDEX_NAME == "vector_index" and settings.TEXT_INDEX_NAME == "text_search_index"
    assert settings.LEGACY_EMBEDDING_MODEL_ID == "gte-multilingual-base"
    assert settings.LOCAL_STORAGE_DIR_MODE == "770"
    assert settings.EMBEDDING_DEVICE is None and settings.EMBEDDING_TRUST_REMOTE_CODE is True
    assert settings.HOST == "0.0.0.0" and settings.PORT == 8001


def test_the_directory_mode_is_validated_when_settings_load():
    assert Settings(_env_file=None, LOCAL_STORAGE_DIR_MODE="0775").LOCAL_STORAGE_DIR_MODE == "0775"
    with pytest.raises(ValueError, match="octal"):
        Settings(_env_file=None, LOCAL_STORAGE_DIR_MODE="999")


def test_the_parser_accepts_exactly_the_extensions_it_always_did():
    assert set(EXTRACTORS) == {
        ".pdf", ".docx", ".doc", ".png", ".jpeg", ".jpg", ".webp", ".txt", ".xlsx", ".pptx", ".ppt",
        ".html", ".htm", ".eml", ".msg",
    }
