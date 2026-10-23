"""The FastAPI `lifespan`: brings up the backing services and model clients at startup and tears them down in reverse."""
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.config import Settings, get_settings
from app.logic.llm_models import HELPER_ROLES, helper_model_names, reasoning_efforts
from app.observability import apply_log_levels
from app.routers.admin import purge_old_rows
from app.services.db.database_factory import create_database_service
from app.services.db.schema_migrator import SchemaError, ensure_schema
from app.services.kb.kb_catalog import KbCatalog, make_describer
from app.services.llm.chat_llm import ChatLLM
from app.services.llm.chat_llm_factory import (
    chat_llm_accepts_reasoning_effort,
    chat_llm_endpoint,
    chat_llm_model_name,
    create_chat_model,
    resolve_llm_type,
)
from app.services.queue.queue_factory import create_queue_service
from app.services.storage.storage_factory import create_storage_service
from app.logic.ingest_recovery import recover_stuck_files
from app.services.vector.search_runtime import create_search_runtime, keep_search_warm

logger = logging.getLogger("app")


def _llm_http_client(settings: Settings) -> httpx.AsyncClient | None:
    """One HTTP client for every model call, or None to leave the SDK's own.

    The SDK's default client drops a connection after 5 seconds idle, so between
    chat turns every call opened a new one: a TCP and TLS handshake, about a
    second on a 400 ms round trip, and it landed on the critical path twice per
    turn (the first helper call, and the answer, which had a client of its own).
    Sharing one client with a long keep-alive, and keeping a few connections
    open with `_keep_llm_connections_warm`, takes that off the turn.
    """
    if settings.LLM_WARM_CONNECTIONS <= 0:
        return None
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=settings.LLM_HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=settings.LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=settings.LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS,
        ),
        # The SDK's own defaults, which a client passed in would otherwise replace.
        timeout=httpx.Timeout(
            settings.LLM_HTTP_TIMEOUT_SECONDS, connect=settings.LLM_HTTP_CONNECT_TIMEOUT_SECONDS,
        ),
    )


async def _keep_llm_connections_warm(
    client: httpx.AsyncClient, url: str, connections: int, ping_interval: float,
) -> None:
    """Holds `connections` connections to the model endpoint open.

    A turn makes up to three model calls at once or back to back, so one warm
    connection is not enough. This sends that many token-free requests together
    (each needs its own connection), now and then every ping_interval seconds
    (Settings.LLM_WARM_PING_INTERVAL_SECONDS), and any HTTP answer at all - even
    a 404 - does the job. Costs nothing per request, and a failure only means
    the next real call connects the slow way.
    """
    while True:
        try:
            await asyncio.gather(*(client.get(url) for _ in range(connections)))
        except Exception as e:
            logger.debug("LLM connection keep-alive failed (%s); the next call will reconnect.", e)
        await asyncio.sleep(ping_interval)


def _load_settings() -> Settings:
    try:
        settings = get_settings()
    except Exception as e:
        logger.exception("CRITICAL: Failed to load settings during application startup!")
        raise RuntimeError(f"Failed to load application settings: {e}") from e
    apply_log_levels(settings)
    logger.info("Using the following settings:\n" + "\n".join(f"  - {key}: {getattr(settings, key)}" for key in Settings.model_fields))
    return settings


async def _apply_schema(settings: Settings) -> None:
    """Creates the database and applies pending scripts before the connection pool, which needs the
    database to exist. A failure stops startup with the reason rather than serving against a half-built schema."""
    if not settings.AUTO_APPLY_SCHEMA:
        logger.info("Schema auto-apply skipped (AUTO_APPLY_SCHEMA=False).")
        return
    try:
        await asyncio.to_thread(
            ensure_schema, settings.DB_CONNECTION_STRING.get_secret_value(),
            login_timeout_seconds=settings.DB_LOGIN_TIMEOUT_SECONDS,
            lock_timeout_seconds=settings.SCHEMA_LOCK_TIMEOUT_SECONDS,
            tls_ca_file=settings.DB_TLS_CA_FILE,
        )
    except SchemaError as e:
        logger.error("Database schema could not be applied: %s", e)
        raise RuntimeError(f"Database schema could not be applied: {e}") from e


async def _purge_old_rows(db_service, settings: Settings) -> None:
    """Applies AUDIT_RETENTION_DAYS / TRACE_RETENTION_DAYS once at startup. Housekeeping: a failure is logged, not fatal."""
    try:
        removed = await purge_old_rows(db_service, settings)
        if any(removed.values()):
            logger.info("Retention purge removed %s.", removed)
    except Exception:
        logger.warning("Retention purge failed; leaving the audit and trace rows as they are.", exc_info=True)


async def _ingest_recovery_loop(db_service, queue, settings: Settings) -> None:
    interval = settings.INGEST_RECOVERY_INTERVAL_SECONDS
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            n = await recover_stuck_files(db_service, queue, settings)
            if n:
                logger.info("Ingest recovery: requeued %d stuck file(s).", n)
        except Exception:
            logger.warning("Ingest recovery loop error.", exc_info=True)


async def _started(service):
    await service.initialize()
    return service


def _build_helper_clients(settings: Settings, http_client: httpx.AsyncClient | None) -> tuple[dict, dict]:
    """({role: client} for each helper role with a model of its own, {role: model name or None}).

    One client is built per distinct model name, however many roles share it. A helper model that
    cannot be built is not fatal, unlike the main one: it is a typo in an optional optimisation, and
    the step falls back to the model that writes the answer. Caching the failure keeps a name shared
    by two roles from being tried twice.
    """
    names = helper_model_names({role: getattr(settings, f"LLM_{role.upper()}_MODEL") for role in HELPER_ROLES}, settings.LLM_HELPER_MODEL)
    built: dict = {}
    clients: dict = {}
    for role, name in names.items():
        if name is None:
            continue
        if name not in built:
            try:
                built[name] = create_chat_model(settings, model=name, http_client=http_client)
            except Exception:
                logger.exception("Model %r is unavailable; the steps using it fall back to the main model.", name)
                built[name] = None
        if built[name] is not None:
            clients[role] = built[name]
    return clients, names


def start_llm(app: FastAPI, settings: Settings) -> tuple[httpx.AsyncClient | None, asyncio.Task | None]:
    """Puts the model clients on app.state, where services.llm.chat_llm.get_chat_llm reads them.

    `chat_model` writes the answer; `task_clients` maps a helper role to the lighter model configured
    for it (see "Chat: which model does what" in config.py), and a role with no entry runs on the main model.
    Returns the shared HTTP client behind them all and the task keeping its connections warm.
    """
    http_client = _llm_http_client(settings)
    # Raises if the backend is misconfigured, the same as the factories: a chatbot with no
    # model is not a degraded service, it is a stopped one, and the message names the missing setting.
    app.state.chat_model = create_chat_model(settings, http_client=http_client)

    warmer = None
    endpoint = chat_llm_endpoint(settings)
    if http_client is not None and endpoint:
        warmer = asyncio.create_task(_keep_llm_connections_warm(
            http_client, endpoint, settings.LLM_WARM_CONNECTIONS, ping_interval=settings.LLM_WARM_PING_INTERVAL_SECONDS,
        ))

    app.state.task_clients, names = _build_helper_clients(settings, http_client)
    # Optional, and like a helper model not fatal: a typo here costs the fallback, not the service.
    app.state.fallback_model = None
    if (settings.LLM_FALLBACK_MODEL or "").strip():
        try:
            app.state.fallback_model = create_chat_model(settings, model=settings.LLM_FALLBACK_MODEL.strip(), http_client=http_client)
        except Exception:
            logger.exception("LLM_FALLBACK_MODEL %r is unavailable; answers have no fallback.", settings.LLM_FALLBACK_MODEL)
    logger.info(
        "LLM models: backend=%s answer=%s %s",
        resolve_llm_type(settings), chat_llm_model_name(settings),
        " ".join(f"{role}={names[role] or 'main'}" for role in HELPER_ROLES),
    )
    accepted = chat_llm_accepts_reasoning_effort(settings)
    app.state.helper_reasoning_effort, app.state.answer_reasoning_effort = reasoning_efforts(
        settings.LLM_AUX_REASONING_EFFORT, settings.LLM_ANSWER_REASONING_EFFORT, accepted,
    )
    if not accepted and (settings.LLM_AUX_REASONING_EFFORT or settings.LLM_ANSWER_REASONING_EFFORT):
        logger.info("LLM_TYPE=%s takes no reasoning_effort; LLM_*_REASONING_EFFORT is ignored.", resolve_llm_type(settings))
    return http_client, warmer


def start_catalog(app: FastAPI, settings: Settings, storage, db_service) -> None:
    """The knowledge-base catalog: what the bot can say it knows (services/kb/kb_catalog.py). It needs a model
    to write the descriptions, and there is always one by now - create_chat_model raised if not."""
    describer = make_describer(ChatLLM(
        chat_model=app.state.chat_model,
        task_clients=app.state.task_clients,
        helper_reasoning_effort=app.state.helper_reasoning_effort,
    ))
    app.state.kb_catalog = KbCatalog(storage, db_service, describer, settings)
    # Once at startup, in the background: creates it on first run and describes documents indexed before it existed.
    app.state.kb_catalog_task = asyncio.create_task(app.state.kb_catalog.refresh())


def _start_search(app: FastAPI, settings: Settings) -> asyncio.Task | None:
    """Builds the search runtime (embedding client + lazily-connected vector store,
    see app/services/vector/search_runtime.py) and, if configured, starts the
    background task that keeps it warm - the same idea as _keep_llm_connections_warm,
    for the one RPC that used to sit on a chat turn's critical path and now runs
    in-process instead."""
    app.state.search_runtime = create_search_runtime(settings)
    if settings.SEARCH_WARMUP_SECONDS <= 0:
        return None
    return asyncio.create_task(keep_search_warm(
        app.state.search_runtime, settings.MONGODB_COLLECTION_NAME, settings.SEARCH_WARMUP_SECONDS,
    ))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown of the application."""
    settings = _load_settings()
    await _apply_schema(settings)

    # Backends are chosen by DB_TYPE / STORAGE_TYPE; see the factories.
    db_service = app.state.db_service = await _started(create_database_service(settings))
    storage = app.state.file_storage_service = await _started(create_storage_service(settings))
    queue = app.state.redis_chatbot_queue = await _started(create_queue_service(settings))
    llm_http_client, llm_warmer = start_llm(app, settings)
    start_catalog(app, settings, storage, db_service)
    search_warmer = _start_search(app, settings)
    await _purge_old_rows(db_service, settings)

    # Startup sweep: requeue any files left stuck from a previous worker crash or
    # deploy. Runs before the app begins serving so the files are not silently
    # broken from the moment the pod comes up.
    try:
        n = await recover_stuck_files(db_service, queue, settings)
        if n:
            logger.warning("Startup ingest recovery: requeued %d stuck file(s).", n)
    except Exception:
        logger.warning("Startup ingest recovery failed; files may still be stuck.", exc_info=True)

    recovery_task = asyncio.create_task(_ingest_recovery_loop(db_service, queue, settings))

    logger.info("File ingestion is handled by the parsing-indexing-service worker (celery -A app.celery_app worker).")

    yield

    logger.info("Application shutdown...")
    recovery_task.cancel()
    # Disconnect services in reverse order of initialization
    if search_warmer is not None:
        search_warmer.cancel()
    await app.state.search_runtime.close()
    if llm_warmer is not None:
        llm_warmer.cancel()
    if llm_http_client is not None:
        await llm_http_client.aclose()
    await queue.close()
    await storage.close()
    await db_service.close()
    logger.info("Application shutdown complete.")
