"""What startup actually puts on app.state for the LLM.

The factory tests cover which client a setting builds. This covers the block in
lifespan_manager that consumes it: that one client is built per distinct model
name however many roles share it, that a main backend which cannot be built
stops startup, that a helper model which cannot be built does not, and that the
connection warmer starts only when there is a remote endpoint to warm.

Everything the LLM block does not touch - database, storage, queue, the KB
catalog and worker - is stubbed, so no infrastructure is needed. Nothing here
reaches the network: the LangChain constructors do not call the API, and the
warmer task is cancelled before its first ping.
"""

import asyncio

import app.lifespan_manager as lifespan_manager
import pytest
from app.config import Settings
from app.lifespan_manager import lifespan
from app.services.vector.search_runtime import SearchRuntime

AZURE = {
    "AZURE_OPENAI_API_KEY": "key",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
    "AZURE_OPENAI_DEPLOYMENT_NAME": "gpt-5-mini",
}
GOOGLE = {"GOOGLE_API_KEY": "AIza-example", "GOOGLE_MODEL_NAME": "gemini-2.5-flash"}
OLLAMA = {"MODEL_NAME": "phi4", "LLM_API_URL": "http://127.0.0.1:11434"}
LLM_ENV_NAMES = tuple(AZURE) + tuple(OLLAMA) + tuple(GOOGLE) + (
    "LLM_AUX_REASONING_EFFORT", "LLM_ANSWER_REASONING_EFFORT",
    "LLM_TYPE", "LITELLM_BASE_URL", "LITELLM_API_KEY", "LITELLM_MODEL_NAME",
    "LLM_HELPER_MODEL", "LLM_REWRITE_MODEL", "LLM_RERANK_MODEL",
    "LLM_TRANSLATE_MODEL", "LLM_INTENT_MODEL", "LLM_DESCRIBE_MODEL",
)


class _App:
    """Stands in for FastAPI's app - lifespan only ever touches app.state."""

    class state:
        pass


class _Stub:
    """An infrastructure service that initialises and closes without doing anything."""

    async def initialize(self):
        return self

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def _stub_everything_but_the_llm(monkeypatch):
    for name in LLM_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for factory in ("create_database_service", "create_storage_service", "create_queue_service"):
        monkeypatch.setattr(lifespan_manager, factory, lambda *a, **k: _Stub())
    # The schema step would connect to a real server (see test_schema_migrator.py).
    monkeypatch.setattr(lifespan_manager, "ensure_schema", lambda *a, **k: [])
    # Search is unrelated to what this file tests; a real SearchRuntime with a dummy
    # embedding client never touches the network unless something actually searches,
    # which nothing here does - the vector store connects lazily, on first search.
    monkeypatch.setattr(lifespan_manager, "create_search_runtime", lambda settings: SearchRuntime(settings=settings, embedding=_Stub()))
    # The catalog kicks off a background refresh that would reach the stubs.
    import app.services.kb.kb_catalog as kb_catalog
    monkeypatch.setattr(kb_catalog.KbCatalog, "refresh", lambda self: asyncio.sleep(0))


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        REDIS_URL="redis://localhost:6379/0",
        CHATBOT_QUEUE_NAME="q",
        DB_CONNECTION_STRING="x",
        SECRET_KEY="x",
        KB_WORKER_ENABLED=False,
    )
    return Settings(**{**base, **overrides})


async def _start(monkeypatch, settings):
    """Runs startup and hands back app.state, then shuts down cleanly."""
    monkeypatch.setattr(lifespan_manager, "get_settings", lambda: settings)
    app = _App()
    ctx = lifespan(app)
    await ctx.__aenter__()
    try:
        return app.state, ctx
    except Exception:  # pragma: no cover - defensive
        await ctx.__aexit__(None, None, None)
        raise


async def _stop(ctx):
    await ctx.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_startup_puts_the_configured_client_on_app_state(monkeypatch):
    from langchain_openai import AzureChatOpenAI

    state, ctx = await _start(monkeypatch, _settings(LLM_TYPE="azure", **AZURE))
    try:
        assert isinstance(state.chat_model, AzureChatOpenAI)
        assert state.task_clients == {}, "no helper model configured, so no helper clients"
    finally:
        await _stop(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides,expected", [
    (dict(LLM_TYPE="azure", **AZURE), "minimal"),
    (dict(LLM_TYPE="google", **GOOGLE), None),
], ids=["azure-keeps-it", "google-drops-it"])
async def test_reasoning_effort_is_set_only_for_a_backend_that_takes_it(monkeypatch, overrides, expected):
    """The setting defaults to 'minimal', so a Gemini or Bedrock deployment inherits it.

    Passing it on would fail every helper and answer call, so startup leaves it
    unset for a backend whose API has no such argument.
    """
    state, ctx = await _start(monkeypatch, _settings(
        **overrides, LLM_AUX_REASONING_EFFORT="minimal", LLM_ANSWER_REASONING_EFFORT="minimal",
    ))
    try:
        assert state.helper_reasoning_effort == expected
        assert state.answer_reasoning_effort == expected
    finally:
        await _stop(ctx)


@pytest.mark.asyncio
async def test_roles_sharing_a_model_share_one_client(monkeypatch):
    """The dedup: two roles, one name, one client object - not two."""
    settings = _settings(
        LLM_TYPE="azure", **AZURE,
        LLM_REWRITE_MODEL="gpt-5-nano",
        LLM_RERANK_MODEL="gpt-5-nano",
        LLM_TRANSLATE_MODEL="gpt-5-micro",
    )
    state, ctx = await _start(monkeypatch, settings)
    try:
        assert state.task_clients["rewrite"] is state.task_clients["rerank"]
        assert state.task_clients["translate"] is not state.task_clients["rewrite"]
        assert set(state.task_clients) == {"rewrite", "rerank", "translate"}
    finally:
        await _stop(ctx)


@pytest.mark.asyncio
async def test_a_main_backend_that_cannot_be_built_stops_startup(monkeypatch):
    """A chatbot with no model is a stopped service, not a degraded one."""
    settings = _settings(LLM_TYPE="azure", **{**AZURE, "AZURE_OPENAI_API_KEY": None})
    monkeypatch.setattr(lifespan_manager, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
        await lifespan(_App()).__aenter__()


@pytest.mark.asyncio
async def test_a_helper_model_that_cannot_be_built_does_not(monkeypatch):
    """It is a typo in an optional optimisation; that step uses the main model.

    The role is simply absent from task_clients, which is how ChatLLM
    decides to fall back (see uses_helper).
    """
    def _explode(settings, model=None, http_client=None):
        if model == "does-not-exist":
            raise RuntimeError("no such deployment")
        return real(settings, model=model, http_client=http_client)

    real = lifespan_manager.create_chat_model
    monkeypatch.setattr(lifespan_manager, "create_chat_model", _explode)

    settings = _settings(LLM_TYPE="azure", **AZURE, LLM_REWRITE_MODEL="does-not-exist")
    state, ctx = await _start(monkeypatch, settings)
    try:
        assert state.chat_model is not None, "the answer model still built"
        assert "rewrite" not in state.task_clients, "the bad role fell back to the main model"
    finally:
        await _stop(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,warmed",
    [
        (dict(LLM_TYPE="azure", **AZURE), True),
        (dict(LLM_TYPE="ollama", **OLLAMA), False),
    ],
    ids=["azure-warms", "ollama-does-not"],
)
async def test_the_warmer_starts_only_for_a_remote_endpoint(monkeypatch, overrides, warmed):
    """Ollama takes no shared HTTP client, so there is nothing to keep warm."""
    started = []
    monkeypatch.setattr(
        lifespan_manager, "_keep_llm_connections_warm",
        lambda client, url, connections, **kwargs: started.append(url) or asyncio.sleep(0),
    )
    state, ctx = await _start(monkeypatch, _settings(**overrides))
    try:
        assert bool(started) is warmed
        if warmed:
            assert started == [AZURE["AZURE_OPENAI_ENDPOINT"]]
    finally:
        await _stop(ctx)
