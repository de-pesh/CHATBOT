"""The backend selector, and the bug it exists to prevent.

Before LLM_TYPE, three separate chains in lifespan_manager.py answered "which
backend", "which URL to keep warm" and "which model name to log", and they did
not require the same settings - a LiteLLM block missing only its API key built
an Azure client while the log named the LiteLLM model.
`test_a_half_configured_block_cannot_silently_become_another_backend` is that
case: naming the backend is what makes all three agree.

Nothing here reaches the network: none of these LangChain constructors call the
API.
"""

import pytest
from app.config import Settings
from app.services.llm import (
    aws_llm,
    azure_llm,
    chat_llm_factory,
    google_llm,
    litellm_llm,
    ollama_llm,
)
from app.services.llm.chat_llm_factory import (
    chat_llm_accepts_reasoning_effort,
    chat_llm_endpoint,
    chat_llm_model_name,
    create_chat_model,
    resolve_llm_type,
)
from pydantic import ValidationError

AZURE = {
    "AZURE_OPENAI_API_KEY": "key",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
    "AZURE_OPENAI_DEPLOYMENT_NAME": "gpt-5-mini",
}
LITELLM = {
    "LITELLM_BASE_URL": "http://litellm:4000",
    "LITELLM_API_KEY": "sk-proxy",
    "LITELLM_MODEL_NAME": "gpt-5-mini",
}
OLLAMA = {"MODEL_NAME": "phi4", "LLM_API_URL": "http://127.0.0.1:11434"}
AWS = {"AWS_REGION": "us-east-1", "AWS_BEDROCK_MODEL_ID": "anthropic.claude-3-5-sonnet-20241022-v2:0"}
AWS_KEYS = {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "AWS_SECRET_ACCESS_KEY": "secret"}
GOOGLE = {"GOOGLE_API_KEY": "AIza-example", "GOOGLE_MODEL_NAME": "gemini-2.5-flash"}

VENDOR_MODULES = (azure_llm, litellm_llm, ollama_llm, aws_llm, google_llm)
LLM_ENV_NAMES = (
    tuple(AZURE) + tuple(LITELLM) + tuple(OLLAMA) + tuple(AWS) + tuple(AWS_KEYS) + tuple(GOOGLE)
    + ("AWS_SESSION_TOKEN", "LLM_TYPE")
)


@pytest.fixture(autouse=True)
def _no_ambient_llm_config(monkeypatch):
    """Nothing from the developer's shell may reach these tests.

    Settings reads env_file=("../.env", ".env") and the repo-root .env holds real
    Azure credentials, so "azure with no key raises" would otherwise pass or fail
    depending on whose machine it ran on. _settings() disables the files; this
    clears the same names out of the environment, which pydantic reads first.
    """
    for name in LLM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides) -> Settings:
    """Settings with no .env files read and only the required fields filled in."""
    base = dict(
        _env_file=None,
        REDIS_URL="redis://localhost:6379/0",
        CHATBOT_QUEUE_NAME="q",
        DB_CONNECTION_STRING="x",
        SECRET_KEY="x",
    )
    return Settings(**{**base, **overrides})


# --------------------------------------------------------------- each backend

def test_azure_is_selected_and_described_consistently():
    from langchain_openai import AzureChatOpenAI

    settings = _settings(LLM_TYPE="azure", **AZURE)
    assert isinstance(create_chat_model(settings), AzureChatOpenAI)
    assert chat_llm_endpoint(settings) == AZURE["AZURE_OPENAI_ENDPOINT"]
    assert chat_llm_model_name(settings) == "gpt-5-mini"


def test_litellm_is_selected_and_described_consistently():
    from langchain_openai import AzureChatOpenAI, ChatOpenAI

    settings = _settings(LLM_TYPE="litellm", **LITELLM)
    client = create_chat_model(settings)
    assert isinstance(client, ChatOpenAI) and not isinstance(client, AzureChatOpenAI)
    assert chat_llm_endpoint(settings) == LITELLM["LITELLM_BASE_URL"]
    assert chat_llm_model_name(settings) == "gpt-5-mini"


def test_ollama_is_selected_and_keeps_no_connections_warm():
    """endpoint() is None for Ollama, which is what keeps the warmer switched off.

    ChatOllama takes no shared HTTP client, so a warmer would hold connections
    the model calls never use.
    """
    from langchain_ollama import ChatOllama

    settings = _settings(LLM_TYPE="ollama", **OLLAMA)
    assert isinstance(create_chat_model(settings), ChatOllama)
    assert chat_llm_endpoint(settings) is None
    assert chat_llm_model_name(settings) == "phi4"


def test_aws_is_selected_and_keeps_no_connections_warm():
    """Bedrock through the Converse API. boto3 owns its connections, so no warmer."""
    from langchain_aws import ChatBedrockConverse

    settings = _settings(LLM_TYPE="aws", **AWS, **AWS_KEYS)
    client = create_chat_model(settings)
    assert isinstance(client, ChatBedrockConverse)
    assert client.model_id == AWS["AWS_BEDROCK_MODEL_ID"]
    assert client.region_name == AWS["AWS_REGION"]
    assert chat_llm_endpoint(settings) is None
    assert chat_llm_model_name(settings) == AWS["AWS_BEDROCK_MODEL_ID"]


def test_aws_needs_no_keys_when_a_role_supplies_credentials():
    """The cluster case: region and model only, boto3's default chain does the rest."""
    from langchain_aws import ChatBedrockConverse

    assert isinstance(create_chat_model(_settings(LLM_TYPE="aws", **AWS)), ChatBedrockConverse)


@pytest.mark.parametrize("half", [
    {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE"},
    {"AWS_SECRET_ACCESS_KEY": "secret"},
], ids=["key-id-only", "secret-only"])
def test_aws_rejects_half_a_key_pair(half):
    """Half a pair is a typo, not a request for the default chain."""
    with pytest.raises(ValueError, match="AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY together"):
        create_chat_model(_settings(LLM_TYPE="aws", **AWS, **half))


def test_google_is_selected_and_keeps_no_connections_warm():
    from langchain_google_genai import ChatGoogleGenerativeAI

    settings = _settings(LLM_TYPE="google", **GOOGLE)
    client = create_chat_model(settings)
    assert isinstance(client, ChatGoogleGenerativeAI)
    assert client.model.endswith(GOOGLE["GOOGLE_MODEL_NAME"])
    assert chat_llm_endpoint(settings) is None
    assert chat_llm_model_name(settings) == GOOGLE["GOOGLE_MODEL_NAME"]


@pytest.mark.parametrize("llm_type,expected,overrides", [
    ("aws", "AWS_REGION, AWS_BEDROCK_MODEL_ID", {}),
    ("google", "GOOGLE_API_KEY, GOOGLE_MODEL_NAME", {}),
], ids=["aws", "google"])
def test_a_new_vendor_with_nothing_set_names_what_it_needs(llm_type, expected, overrides):
    with pytest.raises(ValueError, match=f"LLM_TYPE={llm_type!r} requires {expected}"):
        create_chat_model(_settings(LLM_TYPE=llm_type, **overrides))


def test_a_helper_model_reuses_the_backend_with_a_different_name():
    settings = _settings(LLM_TYPE="azure", **AZURE)
    client = create_chat_model(settings, model="gpt-5-nano")
    assert client.deployment_name == "gpt-5-nano"


# ------------------------------------------------------------ misconfiguration

def test_a_missing_credential_names_itself():
    settings = _settings(LLM_TYPE="azure", **{**AZURE, "AZURE_OPENAI_API_KEY": None})
    with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
        create_chat_model(settings)


def test_an_unknown_backend_is_rejected_by_the_type():
    with pytest.raises(ValidationError):
        _settings(LLM_TYPE="bogus")


def test_the_factory_rejects_an_unknown_backend_too():
    """The Literal catches it first, so this covers the factory's own guard."""
    with pytest.raises(ValueError, match="Unsupported LLM_TYPE"):
        chat_llm_factory._module("bogus")


# ------------------------------------------------- nothing is ever inferred

def test_a_half_configured_block_cannot_silently_become_another_backend():
    """The original bug, as a test.

    LiteLLM has a base URL and a model name but no key, and Azure is complete.
    The old code inferred the backend from whichever block looked populated and
    built an Azure client, while the startup log named the LiteLLM model and the
    warmer pinged Azure - three chains, three different conditions. Naming the
    backend is what makes all three agree, whichever one is named.
    """
    from langchain_openai import AzureChatOpenAI

    half = dict(
        LITELLM_BASE_URL=LITELLM["LITELLM_BASE_URL"],
        LITELLM_MODEL_NAME=LITELLM["LITELLM_MODEL_NAME"],
    )
    # Naming azure: all three answers are azure, and the stray LiteLLM vars are
    # simply not consulted.
    settings = _settings(LLM_TYPE="azure", **half, **AZURE)
    assert isinstance(create_chat_model(settings), AzureChatOpenAI)
    assert chat_llm_endpoint(settings) == AZURE["AZURE_OPENAI_ENDPOINT"]
    assert chat_llm_model_name(settings) == "gpt-5-mini"

    # Naming litellm: it is not silently swapped for the complete Azure block -
    # the missing key is reported instead.
    with pytest.raises(ValueError, match="LITELLM_API_KEY"):
        create_chat_model(_settings(LLM_TYPE="litellm", **half, **AZURE))


def test_a_complete_second_backend_does_not_override_the_named_one():
    """Both fully configured: the setting decides, not a priority order."""
    from langchain_openai import AzureChatOpenAI, ChatOpenAI

    both = {**LITELLM, **AZURE}
    azure_client = create_chat_model(_settings(LLM_TYPE="azure", **both))
    litellm_client = create_chat_model(_settings(LLM_TYPE="litellm", **both))
    assert isinstance(azure_client, AzureChatOpenAI)
    assert isinstance(litellm_client, ChatOpenAI) and not isinstance(litellm_client, AzureChatOpenAI)


# ------------------------------------------------- reasoning_effort per backend

@pytest.mark.parametrize("overrides,accepts", [
    (dict(LLM_TYPE="azure", **AZURE), True),
    (dict(LLM_TYPE="litellm", **LITELLM), True),
    (dict(LLM_TYPE="ollama", **OLLAMA), True),
    (dict(LLM_TYPE="aws", **AWS), False),
    (dict(LLM_TYPE="google", **GOOGLE), False),
], ids=["azure", "litellm", "ollama", "aws", "google"])
def test_only_backends_that_take_reasoning_effort_get_it(overrides, accepts):
    """ChatLLM binds reasoning_effort to whatever client it is handed.

    Bedrock's Converse API and Gemini have no such argument, and the default
    LLM_AUX_REASONING_EFFORT is 'minimal', so without this a backend switch to
    either would fail on every helper call.
    """
    assert chat_llm_accepts_reasoning_effort(_settings(**overrides)) is accepts


def test_the_default_backend_is_azure_and_says_what_it_needs():
    """An unset LLM_TYPE gets a named backend, not a guess at one."""
    settings = _settings()
    assert resolve_llm_type(settings) == "azure"
    with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
        create_chat_model(settings)


# ---------------------------------------------------------- the vendor contract

@pytest.mark.parametrize("mod", VENDOR_MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_every_vendor_module_honours_the_contract(mod):
    """What a Protocol would check, if the lazy if-chain ever bound a module.

    chat_llm_factory's docstring is the contract; this is its enforcement.
    """
    for name in ("build", "endpoint", "model_name", "required_settings"):
        assert callable(getattr(mod, name, None)), f"{mod.__name__} is missing {name}()"
    required = mod.required_settings()
    assert isinstance(required, tuple) and required
    assert isinstance(getattr(mod, "ACCEPTS_REASONING_EFFORT", True), bool)
    for setting in required:
        assert setting in Settings.model_fields, f"{mod.__name__} requires unknown setting {setting}"
