"""The vision backend selector, and the one rule it does not share with chat.

The chat factory raises whenever it cannot build a model, because a chatbot with
no model is a stopped service. This one must not: the vision model is only used
for ParseFile's scanned-page/image OCR fallback, and every other format parses
without it. So "named a backend that cannot be built" raises, and "no backend
configured at all" returns None with a warning.

Nothing here reaches the network.
"""

import pytest
from app.config import Settings
from app.parsing.vision import (
    azure_vision_llm,
    foundry_vision_llm,
    litellm_vision_llm,
    ollama_vision_llm,
    openai_vision_llm,
    vision_llm_factory,
)
from app.parsing.vision.vision_llm_factory import create_vision_llm_client
from pydantic import ValidationError

AZURE = {
    "AZURE_OPENAI_API_KEY": "key",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
    "AZURE_OPENAI_DEPLOYMENT_NAME": "gpt-4o",
}
LITELLM = {
    "LITELLM_BASE_URL": "http://litellm:4000",
    "LITELLM_API_KEY": "sk-proxy",
    "LITELLM_MODEL_NAME": "gpt-4o",
}
OPENAI = {
    "VISION_OPENAI_BASE_URL": "https://res.openai.azure.com/openai/v1",
    "VISION_OPENAI_API_KEY": "key",
    "VISION_OPENAI_MODEL": "my-deployment",
}
OLLAMA = {"OLLAMA_BASE_URL": "http://127.0.0.1:11434", "OLLAMA_MODEL": "llama3.2-vision:11b"}

VENDOR_MODULES = (azure_vision_llm, foundry_vision_llm, litellm_vision_llm, ollama_vision_llm, openai_vision_llm)
LLM_ENV_NAMES = tuple(AZURE) + tuple(LITELLM) + tuple(OLLAMA) + tuple(OPENAI) + (
    "AZURE_AI_FOUNDRY_ENDPOINT", "AZURE_AI_FOUNDRY_API_KEY", "AZURE_AI_FOUNDRY_MODEL",
    "VISION_LLM_TYPE",
)


@pytest.fixture(autouse=True)
def _no_ambient_llm_config(monkeypatch):
    """Nothing from the developer's shell or .env may reach these tests."""
    for name in LLM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# --------------------------------------------------------------- each backend

def test_azure_is_selected():
    from langchain_openai import AzureChatOpenAI

    client = create_vision_llm_client(_settings(VISION_LLM_TYPE="azure", **AZURE))
    assert isinstance(client, AzureChatOpenAI)


def test_litellm_is_selected():
    from langchain_openai import AzureChatOpenAI, ChatOpenAI

    client = create_vision_llm_client(_settings(VISION_LLM_TYPE="litellm", **LITELLM))
    assert isinstance(client, ChatOpenAI) and not isinstance(client, AzureChatOpenAI)


def test_openai_is_selected_with_its_own_settings_not_litellms():
    from langchain_openai import AzureChatOpenAI, ChatOpenAI

    client = create_vision_llm_client(_settings(VISION_LLM_TYPE="openai", **OPENAI))
    assert isinstance(client, ChatOpenAI) and not isinstance(client, AzureChatOpenAI)
    assert client.model_name == "my-deployment"
    assert str(client.openai_api_base) == OPENAI["VISION_OPENAI_BASE_URL"]


def test_ollama_is_selected_with_an_explicit_context_window():
    """num_ctx is set on purpose - Ollama truncates the prompt silently without it."""
    from langchain_ollama import ChatOllama

    settings = _settings(VISION_LLM_TYPE="ollama", **OLLAMA)
    client = create_vision_llm_client(settings)
    assert isinstance(client, ChatOllama)
    assert client.num_ctx == settings.VISION_NUM_CTX


# ------------------------------------------ the rule that differs from chat

def test_the_default_is_no_ocr_and_the_service_still_starts():
    """OCR is opt-in: scanned pages fail, every other format still parses."""
    assert _settings().VISION_LLM_TYPE == "none"
    assert create_vision_llm_client(_settings()) is None


def test_none_is_an_explicit_way_to_say_this_deployment_has_no_ocr():
    assert create_vision_llm_client(_settings(VISION_LLM_TYPE="none", **AZURE)) is None


def test_a_named_backend_that_cannot_be_built_does_raise():
    """Someone asked for this backend; a silent None would hide the typo."""
    settings = _settings(VISION_LLM_TYPE="azure", **{**AZURE, "AZURE_OPENAI_API_KEY": None})
    with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
        create_vision_llm_client(settings)


def test_an_unknown_backend_is_rejected_by_the_type():
    with pytest.raises(ValidationError):
        _settings(VISION_LLM_TYPE="bogus")


def test_the_factory_rejects_an_unknown_backend_too():
    with pytest.raises(ValueError, match="Unsupported VISION_LLM_TYPE"):
        vision_llm_factory._module("bogus")


# ------------------------------------------------- nothing is ever inferred

def test_a_half_configured_block_is_not_swapped_for_a_complete_one():
    """LiteLLM has a base URL and model but no key; Azure is complete."""
    settings = _settings(
        VISION_LLM_TYPE="litellm",
        LITELLM_BASE_URL=LITELLM["LITELLM_BASE_URL"],
        LITELLM_MODEL_NAME=LITELLM["LITELLM_MODEL_NAME"],
        **AZURE,
    )
    assert settings.VISION_LLM_TYPE == "litellm"
    with pytest.raises(ValueError, match="LITELLM_API_KEY"):
        create_vision_llm_client(settings)


def test_a_complete_second_backend_does_not_override_the_named_one():
    """Both fully configured: the setting decides, not a priority order."""
    from langchain_openai import AzureChatOpenAI

    both = {**LITELLM, **AZURE}
    assert isinstance(create_vision_llm_client(_settings(VISION_LLM_TYPE="azure", **both)), AzureChatOpenAI)


# ---------------------------------------------------------- the vendor contract

@pytest.mark.parametrize("mod", VENDOR_MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_every_vendor_module_honours_the_contract(mod):
    for name in ("build", "model_name", "required_settings"):
        assert callable(getattr(mod, name, None)), f"{mod.__name__} is missing {name}()"
    required = mod.required_settings()
    assert isinstance(required, tuple) and required
    for setting in required:
        assert setting in Settings.model_fields, f"{mod.__name__} requires unknown setting {setting}"
