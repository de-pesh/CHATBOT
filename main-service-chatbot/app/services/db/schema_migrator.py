"""Creates the database if it is missing and applies scripts/mssql/*.sql to it.

Two callers share this module: the service itself, which runs `ensure_schema` at
startup so a first deploy needs no separate step, and scripts/apply_schema.py,
the command-line front end (status, baseline, force) that the docker-compose
`db-schema` service and `python local.py db` use.

It needs no `sqlcmd` client: it uses python-tds, the driver the service already
depends on.

Applied scripts are recorded in a `schema_migrations` table so a run only applies
what is new. The scripts are forward-only migrations, so replaying an old one is
not something to rely on. A run holds a database lock, so several replicas
starting together take turns and the later ones find nothing left to do.

The database is created only if the target does not exist; when it does, the user
needs no CREATE DATABASE right, which is the usual case on a managed server.
"""
import logging
from collections.abc import Callable
from pathlib import Path

from app.logic.connection_string import connection_parts, parse_connection_string
from app.logic.sql_scripts import split_statements
from app.services.db.mssql_tls import tls_connect_kwargs

logger = logging.getLogger("app")

SERVICE_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = SERVICE_ROOT / "scripts"
TRACKING_TABLE = "schema_migrations"
LOCK_NAME = "chatbot_schema_migrations"


class SchemaError(RuntimeError):
    """The schema could not be brought up to date; the message says why."""


class MSSQLTarget:
    def __init__(self, params: dict, login_timeout_seconds: int, lock_timeout_seconds: int, tls: dict | None = None):
        import pytds

        self.pytds = pytds
        self.params = params
        # Settings.DB_LOGIN_TIMEOUT_SECONDS / SCHEMA_LOCK_TIMEOUT_SECONDS. Plain parameters, not a
        # Settings import: this module is also driven standalone by scripts/apply_schema.py,
        # outside the FastAPI app and its Settings class.
        self.login_timeout_seconds = login_timeout_seconds
        self.lock_timeout_seconds = lock_timeout_seconds
        self.tls = tls or {}  # extra pytds.connect arguments for Encrypt=yes (services/db/mssql_tls.py)
        self.conn = None

    def _open(self, database: str):
        p = self.params
        return self.pytds.connect(
            server=p["host"], port=p["port"] or 1433, user=p["user"], password=p["password"],
            database=database, autocommit=True, login_timeout=self.login_timeout_seconds, **self.tls,
        )

    def connect(self):
        database = self.params["database"]
        try:
            # Straight to the database first: a managed server's user often has no
            # access to master or no CREATE DATABASE right, and the DBA made this one.
            self.conn = self._open(database)
            return
        except self.pytds.Error as first:
            try:
                admin = self._open("master")
                try:
                    with admin.cursor() as cur:
                        # The IF: a replica starting at the same moment may have made it.
                        cur.execute(f"IF DB_ID(N'{database}') IS NULL CREATE DATABASE [{database}]")
                finally:
                    admin.close()
                self.conn = self._open(database)
            except self.pytds.Error:
                raise first from None  # the original error names the real problem

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def execute(self, statement: str):
        with self.conn.cursor() as cur:
            cur.execute(statement)

    def lock(self):
        # A session-owned lock, so it is held across the scripts' own batches and
        # released with the connection even if this process dies.
        with self.conn.cursor() as cur:
            cur.execute(
                "DECLARE @r INT; "
                "EXEC @r = sp_getapplock @Resource = %s, @LockMode = 'Exclusive', "
                "@LockOwner = 'Session', @LockTimeout = %s; "
                "SELECT @r",
                (LOCK_NAME, self.lock_timeout_seconds * 1000),
            )
            # The DECLARE and EXEC each come back as an empty result set ahead of
            # the SELECT's, and python-tds reads only the first unless told to move on.
            while True:
                try:
                    status = cur.fetchone()[0]
                    break
                except self.pytds.ProgrammingError:
                    if not cur.nextset():
                        raise
        if status < 0:  # -1 timeout, -2 cancelled, -3 deadlock victim, -999 error
            raise SchemaError(f"Timed out after {self.lock_timeout_seconds}s waiting for another instance to finish applying the schema (sp_getapplock returned {status}).")

    def unlock(self):
        with self.conn.cursor() as cur:
            cur.execute("EXEC sp_releaseapplock @Resource = %s, @LockOwner = 'Session'", (LOCK_NAME,))

    def applied(self) -> set[str]:
        self.execute(
            f"IF OBJECT_ID('dbo.{TRACKING_TABLE}', 'U') IS NULL "
            f"CREATE TABLE dbo.{TRACKING_TABLE} (filename NVARCHAR(255) PRIMARY KEY, applied_on DATETIME2 DEFAULT SYSUTCDATETIME())"
        )
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT filename FROM dbo.{TRACKING_TABLE}")
            return {row[0] for row in cur.fetchall()}

    def record(self, filename: str):
        with self.conn.cursor() as cur:
            cur.execute(f"DELETE FROM dbo.{TRACKING_TABLE} WHERE filename = %s", (filename,))
            cur.execute(f"INSERT INTO dbo.{TRACKING_TABLE} (filename) VALUES (%s)", (filename,))


def open_target(connection_string: str, login_timeout_seconds: int, lock_timeout_seconds: int, tls_ca_file: str | None = None):
    """Connects to the target, creating the database if it is missing.

    The timeouts are Settings.DB_LOGIN_TIMEOUT_SECONDS and SCHEMA_LOCK_TIMEOUT_SECONDS; `tls_ca_file` is
    Settings.DB_TLS_CA_FILE.

    Returns (target, params, description of where it connected).
    """
    params = parse_connection_string(connection_string)
    if not params["database"]:
        raise SchemaError("DB_CONNECTION_STRING has no Database= part - nothing to create the schema in.")
    try:
        tls = tls_connect_kwargs(connection_parts(connection_string), tls_ca_file)
    except ValueError as exc:
        raise SchemaError(str(exc)) from exc
    target = MSSQLTarget(params, login_timeout_seconds, lock_timeout_seconds, tls)
    where = f"mssql at {params['host']}:{params['port'] or 'default'}, database {params['database']!r}"
    try:
        target.connect()
    except Exception as exc:  # driver errors differ per backend; the message is what matters
        raise SchemaError(
            f"Cannot reach {where}: {exc}\n"
            "Is the server running, and do the host, port, user and password in DB_CONNECTION_STRING match it? "
            "If the database does not exist and the user may not create one, ask for an empty one to be created."
        ) from exc
    return target, params, where


def list_scripts() -> list[Path]:
    return sorted((SCRIPTS_ROOT / "mssql").glob("*.sql"))


def run_scripts(target, scripts: list[Path], log: Callable[[str], None]) -> None:
    """Runs each script and records it. Stops at the first failure."""
    for script in scripts:
        log(f"applying {script.name} ...")
        try:
            for statement in split_statements(script.read_text(encoding="utf-8")):
                target.execute(statement)
        except Exception as exc:
            raise SchemaError(
                f"{script.name} failed: {exc}\n"
                "Fix the cause and restart; scripts already applied are skipped. "
                "If this database already has the schema (provisioned another way), "
                "run scripts/apply_schema.py --baseline once."
            ) from exc
        target.record(script.name)


def ensure_schema(
    connection_string: str, login_timeout_seconds: int, lock_timeout_seconds: int,
    log: Callable[[str], None] | None = None, tls_ca_file: str | None = None,
) -> list[str]:
    """Brings the database up to date and returns the names of the scripts it applied.

    Blocking (a driver call per statement) - run it off the event loop. Raises
    SchemaError if the database cannot be reached or a script fails, so a service
    that calls it at startup stops with the reason instead of running against a
    half-built schema.

    The two timeouts are Settings.DB_LOGIN_TIMEOUT_SECONDS and SCHEMA_LOCK_TIMEOUT_SECONDS; `tls_ca_file` is
    Settings.DB_TLS_CA_FILE.
    """
    log = log or logger.info
    target, _, where = open_target(connection_string, login_timeout_seconds, lock_timeout_seconds, tls_ca_file)
    try:
        target.lock()
        try:
            done = target.applied()
            pending = [s for s in list_scripts() if s.name not in done]
            if not pending:
                log(f"Schema is up to date ({where}).")
                return []
            run_scripts(target, pending, log)
            log(f"Schema is up to date ({where}); applied {len(pending)} script(s).")
            return [s.name for s in pending]
        finally:
            target.unlock()
    finally:
        target.close()
