"""Chooses the vision LLM backend from configuration.

Picked by VISION_LLM_TYPE. Each backend is one module next to this one -
azure_vision_llm.py, litellm_vision_llm.py, foundry_vision_llm.py,
ollama_vision_llm.py, openai_vision_llm.py - and adding one is that file plus a branch below. The same
shape as main-service-chatbot's services/chat_llm_factory.py, and the same
variable names wherever the backend is the same, so configuring either service's
LLM is one mental model.

A vendor module exposes three module-level functions, as there:

    build(settings) -> BaseChatModel
    model_name(settings) -> str
    required_settings() -> tuple[str, ...]

There is no `model` or `http_client` argument here: nothing in this service
overrides the model per call, and it holds no shared HTTP client.

What differs from the chat factory is what happens when nothing is configured.
This backend is only needed for ParseFile's scanned-page/image vision reading (see
parsing/vision/service.py), and every other file format parses without it, so a missing
one must not stop the service starting over a feature most uploads never touch.
The rule:

    VISION_LLM_TYPE names a backend and its settings are incomplete -> raise.
        Someone asked for this backend and it cannot be built; that is a
        misconfiguration, and it is named at startup.
    VISION_LLM_TYPE='none', the default -> warn, return None. Callers that
        actually need it get a clear error at the point of use instead.
"""
import importlib
import logging

from app.config import Settings
from app.logic.config_checks import require_settings
from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

_NOT_CONFIGURED = (
    "No vision LLM backend configured (VISION_LLM_TYPE=%s). Scanned PDFs and "
    "image uploads will fail; every other file type is unaffected."
)


# VISION_LLM_TYPE -> the vendor module next to this file.
_BACKENDS = {
    "litellm": "litellm_vision_llm",
    "azure": "azure_vision_llm",
    "foundry": "foundry_vision_llm",
    "ollama": "ollama_vision_llm",
    "openai": "openai_vision_llm",
}


def _module(vision_type: str):
    """The vendor module for a backend name.

    Imported only for the selected backend, so an unselected backend's SDK never has
    to be installed - langchain_azure_ai in particular is only needed by one.
    """
    if vision_type not in _BACKENDS:
        raise ValueError(
            f"Unsupported VISION_LLM_TYPE: {vision_type!r}. "
            f"Expected one of: {', '.join(_BACKENDS)}, none."
        )
    return importlib.import_module(f"app.parsing.vision.{_BACKENDS[vision_type]}")


def create_vision_llm_client(settings: Settings) -> BaseChatModel | None:
    """Builds the configured vision-capable chat model, or None if there is none."""
    vision_type = settings.VISION_LLM_TYPE
    if vision_type == "none":
        logger.warning(_NOT_CONFIGURED, settings.VISION_LLM_TYPE)
        return None

    mod = _module(vision_type)
    require_settings(settings, "VISION_LLM_TYPE", *mod.required_settings())

    logger.info("Vision LLM backend: %s (model=%s)", vision_type, mod.model_name(settings))
    return mod.build(settings)
