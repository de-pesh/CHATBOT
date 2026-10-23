"""Chooses the chat LLM backend from configuration.

Picked by LLM_TYPE. Each backend is one module next to this one - azure_llm.py,
litellm_llm.py, ollama_llm.py, aws_llm.py, google_llm.py - and adding one is that
file plus a branch below.
Nothing else in the service names a vendor: services/llm/chat_llm.py and every
route behind it only ever see a LangChain BaseChatModel.

A vendor module exposes exactly four module-level functions:

    build(settings, model=None, http_client=None) -> BaseChatModel
        The client. `model` overrides the configured model name - or, for Azure,
        the deployment name - so a lighter helper model comes from the same
        backend, endpoint and credentials as the main one. `http_client` is
        shared by every client built with it (lifespan_manager._llm_http_client);
        a backend whose SDK cannot take one accepts the argument and ignores it.
    endpoint(settings) -> str | None
        The URL calls go to, for the connection warmer. None means do not warm.
    model_name(settings) -> str
        What the startup log calls the model.
    required_settings() -> tuple[str, ...]
        Setting names that must all be populated. The error message for a
        missing one is built here, not there, so every backend words it the
        same way.

and, optionally, one module-level constant:

    ACCEPTS_REASONING_EFFORT = False
        Defaults to True when absent. ChatLLM binds `reasoning_effort=...`
        to a client when LLM_AUX_REASONING_EFFORT / LLM_ANSWER_REASONING_EFFORT
        are set, and that is an OpenAI-style call argument. A backend whose SDK
        would pass it on to an API that rejects it says so here, and startup then
        never sets the effort for it - the setting is ignored, not fatal.

The backend is named, and the last three questions are answered from that one
name: nothing here infers a backend from which credentials happen to be filled in
(set LITELLM_BASE_URL and LITELLM_MODEL_NAME but no LITELLM_API_KEY and inference
would build an Azure client, log the LiteLLM model, and warm connections to Azure,
with nothing raised).

To add a vendor - Mistral's own API, say - copy aws_llm.py or google_llm.py, the
two most recent, and:

  1. Add its LangChain package to pyproject.toml and regenerate poetry.lock. A
     plain dependency, not an optional extra: switching backend is meant to be a
     config change, and an extra the image was not built with would make it a
     rebuild. The SDK import still stays inside build(), so an unselected
     backend is never imported.
  2. Add its credential fields to config.py beside the LITELLM_* block, and its
     name to the LLM_TYPE Literal. Anything secret is a SecretStr - the startup
     config dump prints every field, and SecretStr is what masks it there.
  3. Write app/services/<vendor>_llm.py implementing the four functions, and set
     ACCEPTS_REASONING_EFFORT = False if its API has no such argument.
  4. Add a branch to _module() below.
  5. Add a case to tests/services/test_chat_llm_factory.py.
  6. Add the keys to .env.sample, docker-compose.yml, openshift/02-configmap.yaml
     and openshift/01-secrets.yaml.

Nothing outside those files changes.
"""
import logging

import httpx
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings

logger = logging.getLogger(__name__)


def _module(llm_type: str):
    """The vendor module for a backend name.

    The import stays inside the branch so an unselected backend's SDK never has
    to be installed - the same reason every other factory in this package does
    it (see services/storage/storage_factory.py).
    """
    if llm_type == "litellm":
        from app.services.llm import litellm_llm
        return litellm_llm
    if llm_type == "azure":
        from app.services.llm import azure_llm
        return azure_llm
    if llm_type == "ollama":
        from app.services.llm import ollama_llm
        return ollama_llm
    if llm_type == "aws":
        from app.services.llm import aws_llm
        return aws_llm
    if llm_type == "google":
        from app.services.llm import google_llm
        return google_llm
    raise ValueError(
        f"Unsupported LLM_TYPE: {llm_type!r}. "
        f"Expected one of: litellm, azure, ollama, aws, google."
    )


def _missing(settings: Settings, mod) -> list:
    """Which of a backend's required settings are not populated.

    Truthiness, so a SecretStr holding an empty string counts as present and
    fails later at the API instead: an empty value in a ConfigMap is a
    different mistake from an absent one.
    """
    return [name for name in mod.required_settings() if not getattr(settings, name, None)]


def resolve_llm_type(settings: Settings) -> str:
    """The backend in use, normalised."""
    return (settings.LLM_TYPE or "").lower()


def _backend(settings: Settings):
    """(backend name, vendor module), with its settings checked.

    Silent on purpose: every public function here goes through it, and
    create_chat_model runs once per distinct model name, so logging would repeat
    itself several times a startup. The one log line lives in create_chat_model.
    """
    llm_type = resolve_llm_type(settings)
    mod = _module(llm_type)
    missing = _missing(settings, mod)
    if missing:
        raise ValueError(f"LLM_TYPE={llm_type!r} requires {', '.join(missing)}.")
    return llm_type, mod


def create_chat_model(
    settings: Settings,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> BaseChatModel:
    """Builds the configured chat model. `model` overrides the model name.

    Raises rather than returning None when the backend is misconfigured, so a
    bad setting stops startup with the name of the setting in it, the way
    create_database_service and create_storage_service do. Starting anyway would
    turn every /chat into a 503.
    """
    llm_type, mod = _backend(settings)
    logger.info("Chat LLM: %s, model %s", llm_type, model or mod.model_name(settings))
    return mod.build(settings, model=model, http_client=http_client)


def chat_llm_endpoint(settings: Settings) -> str | None:
    """The URL the model calls go to, or None when there is nothing to keep warm."""
    return _backend(settings)[1].endpoint(settings)


def chat_llm_model_name(settings: Settings) -> str:
    """The configured model's name, for the startup log."""
    return _backend(settings)[1].model_name(settings)


def chat_llm_accepts_reasoning_effort(settings: Settings) -> bool:
    """Whether `reasoning_effort` may be bound to this backend's client.

    A vendor that cannot take it sets ACCEPTS_REASONING_EFFORT = False; anything
    that says nothing is assumed to (Azure, LiteLLM and Ollama do).
    """
    return getattr(_backend(settings)[1], "ACCEPTS_REASONING_EFFORT", True)
