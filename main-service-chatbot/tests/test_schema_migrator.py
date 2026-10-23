"""Schema creation at startup: what runs, in which order, and when startup stops.

No database is needed. The two vendor targets are replaced by a fake that records
what it was asked to do, so this covers the decisions - create the database on a
first run, apply only what is pending, take the lock around it, stop startup on a
failure - not the SQL, which only a real server can vouch for.
"""

import types

import app.lifespan_manager as lifespan_manager
import app.services.db.schema_migrator as schema_migrator
import pytest
from app.config import Settings
from app.lifespan_manager import lifespan
from app.logic.connection_string import parse_connection_string
from app.services.db.schema_migrator import SchemaError, ensure_schema
from app.services.vector.search_runtime import SearchRuntime


class FakeTarget:
    """Records calls; `applied_names` is what the tracking table already holds."""

    def __init__(self, applied_names=(), fail_on=None):
        self.calls = []
        self.applied_names = set(applied_names)
        self.fail_on = fail_on

    def lock(self):
        self.calls.append("lock")

    def unlock(self):
        self.calls.append("unlock")

    def close(self):
        self.calls.append("close")

    def applied(self):
        self.calls.append("applied")
        return set(self.applied_names)

    def execute(self, statement):
        if self.fail_on and self.fail_on in statement:
            raise RuntimeError("boom")
        self.calls.append(f"exec:{statement}")

    def record(self, filename):
        self.calls.append(f"record:{filename}")


@pytest.fixture
def scripts(tmp_path, monkeypatch):
    """Two mssql scripts, each one statement, in a throwaway scripts directory."""
    folder = tmp_path / "mssql"
    folder.mkdir()
    (folder / "01_a.sql").write_text("CREATE TABLE a (id INT)\nGO\n")
    (folder / "02_b.sql").write_text("CREATE TABLE b (id INT)\nGO\n")
    monkeypatch.setattr(schema_migrator, "SCRIPTS_ROOT", tmp_path)
    return folder


def _use(monkeypatch, target):
    monkeypatch.setattr(
        schema_migrator, "open_target",
        lambda cs, login_timeout, lock_timeout, tls_ca_file=None: (target, {}, "test-db"),
    )


def test_first_run_applies_every_script_under_the_lock(monkeypatch, scripts):
    target = FakeTarget()
    _use(monkeypatch, target)

    assert ensure_schema("cs", 10, 120, log=lambda _: None) == ["01_a.sql", "02_b.sql"]
    assert target.calls == [
        "lock", "applied",
        "exec:CREATE TABLE a (id INT)", "record:01_a.sql",
        "exec:CREATE TABLE b (id INT)", "record:02_b.sql",
        "unlock", "close",
    ]


def test_a_rerun_applies_nothing(monkeypatch, scripts):
    target = FakeTarget(applied_names=["01_a.sql", "02_b.sql"])
    _use(monkeypatch, target)

    assert ensure_schema("cs", 10, 120, log=lambda _: None) == []
    assert not [c for c in target.calls if c.startswith(("exec", "record"))]
    assert target.calls[-2:] == ["unlock", "close"]


def test_only_the_new_script_is_applied(monkeypatch, scripts):
    target = FakeTarget(applied_names=["01_a.sql"])
    _use(monkeypatch, target)

    assert ensure_schema("cs", 10, 120, log=lambda _: None) == ["02_b.sql"]


def test_a_failing_script_raises_and_is_not_recorded(monkeypatch, scripts):
    target = FakeTarget(fail_on="TABLE b")
    _use(monkeypatch, target)

    with pytest.raises(SchemaError, match="02_b.sql failed: boom"):
        ensure_schema("cs", 10, 120, log=lambda _: None)
    assert "record:01_a.sql" in target.calls
    assert "record:02_b.sql" not in target.calls
    assert target.calls[-2:] == ["unlock", "close"], "the lock and connection are released on failure"


def test_a_connection_string_without_a_database_is_refused():
    with pytest.raises(SchemaError, match="no Database="):
        schema_migrator.open_target("Server=x,1433;UserID=u;Password=p", 10, 120)


def test_connection_string_parsing():
    assert parse_connection_string("Server=db,1444;UserID=sa;Password=p;Database=chatbot") == {
        "host": "db", "port": 1444, "user": "sa", "password": "p", "database": "chatbot",
    }


# --- startup wiring -------------------------------------------------------------


class _App:
    class state:
        pass


class _Stub:
    async def initialize(self):
        return self

    async def close(self):
        return None


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None, REDIS_URL="redis://localhost:6379/0", CHATBOT_QUEUE_NAME="q",
        DB_CONNECTION_STRING="Server=db;Database=chatbot", SECRET_KEY="x", KB_WORKER_ENABLED=False,
        LLM_TYPE="azure", AZURE_OPENAI_API_KEY="k", AZURE_OPENAI_ENDPOINT="https://e.openai.azure.com/",
        AZURE_OPENAI_DEPLOYMENT_NAME="d",
    )
    return Settings(**{**base, **overrides})


@pytest.fixture
def order(monkeypatch):
    """Stubs the rest of startup and records whether schema ran before the database pool opened."""
    events = []
    monkeypatch.setattr(lifespan_manager, "create_database_service", lambda *a, **k: types.SimpleNamespace(
        initialize=lambda: _record(events, "db-pool"), close=_Stub().close))
    for factory in ("create_storage_service", "create_queue_service"):
        monkeypatch.setattr(lifespan_manager, factory, lambda *a, **k: _Stub())
    # Search is unrelated to what this file tests; a real SearchRuntime with a dummy
    # embedding client never touches the network unless something actually searches.
    monkeypatch.setattr(lifespan_manager, "create_search_runtime", lambda settings: SearchRuntime(settings=settings, embedding=_Stub()))
    import asyncio

    import app.services.kb.kb_catalog as kb_catalog
    monkeypatch.setattr(kb_catalog.KbCatalog, "refresh", lambda self: asyncio.sleep(0))
    return events


async def _record(events, name):
    events.append(name)


def _schema_recorder(monkeypatch, events):
    monkeypatch.setattr(
        lifespan_manager, "ensure_schema",
        lambda cs, **kwargs: events.append("schema") or [],
    )


@pytest.mark.asyncio
async def test_startup_applies_the_schema_before_opening_the_pool(monkeypatch, order):
    _schema_recorder(monkeypatch, order)
    monkeypatch.setattr(lifespan_manager, "get_settings", lambda: _settings())

    ctx = lifespan(_App())
    await ctx.__aenter__()
    await ctx.__aexit__(None, None, None)

    assert order == ["schema", "db-pool"]


@pytest.mark.asyncio
async def test_startup_skips_the_schema_when_switched_off(monkeypatch, order):
    _schema_recorder(monkeypatch, order)
    monkeypatch.setattr(lifespan_manager, "get_settings", lambda: _settings(AUTO_APPLY_SCHEMA=False))

    ctx = lifespan(_App())
    await ctx.__aenter__()
    await ctx.__aexit__(None, None, None)

    assert order == ["db-pool"]


@pytest.mark.asyncio
async def test_startup_stops_when_the_schema_cannot_be_applied(monkeypatch, order):
    def fail(cs, **kwargs):
        raise SchemaError("no route to host")

    monkeypatch.setattr(lifespan_manager, "ensure_schema", fail)
    monkeypatch.setattr(lifespan_manager, "get_settings", lambda: _settings())

    with pytest.raises(RuntimeError, match="no route to host"):
        await lifespan(_App()).__aenter__()
    assert order == [], "the app must not open the pool against a half-built schema"
