"""
Concrete implementation of the DatabaseService for Microsoft SQL Server.

Uses python-tds (imported as `pytds`): a pure-Python TDS client with no ODBC
driver or system package to install. pyodbc + Microsoft's own ODBC Driver for
SQL Server was the more common choice, but it needs unixODBC and the
msodbcsql package from Microsoft's own repo pulled into the image at build
time - not an option when the build has no route to it, the same constraint
that already keeps indexing-rpc-server's torch install pinned to a mirror.

pytds is synchronous, so every call runs in a worker thread via
asyncio.to_thread rather than blocking the event loop; a small hand-rolled
pool of connections (an asyncio.Queue) stands in for a driver-provided pool.
"""
import asyncio
import logging
from typing import Any

import pytds
from fastapi import HTTPException
from starlette import status as http_status

from app.logic.connection_string import connection_parts, parse_connection_string
from app.services.db.database_service import DatabaseService
from app.services.db.mssql_tls import tls_connect_kwargs

logger = logging.getLogger(__name__)


class MSSQLService(DatabaseService):
    """
    A concrete implementation of the DatabaseService for Microsoft SQL Server.

    Calls every stored procedure the same way: EXEC via
    callproc, then fetchall/nextset in a loop to collect every result set a
    procedure returns (some return one, some - like filter_chatbot_history -
    return a rows set and a separate total_records set).
    """

    def __init__(self, connection_string: str, login_timeout: int, pool_size: int, tls_ca_file: str | None = None):
        """`connection_string` is ADO-style, e.g. "Server=host,1433;UserID=sa;Password=...;Database=chatbot"
        (a ",port" suffix on Server is optional and defaults to 1433). `login_timeout` and `pool_size`
        are Settings.DB_LOGIN_TIMEOUT_SECONDS and MSSQL_POOL_SIZE. `tls_ca_file` is Settings.DB_TLS_CA_FILE, for
        Encrypt=yes without TrustServerCertificate=yes (see mssql_tls)."""
        self.connection_string = connection_string
        self.tls_ca_file = tls_ca_file
        self.login_timeout = login_timeout
        self.pool_size = pool_size
        self.pool: asyncio.Queue | None = None
        self._is_initialized = False

    def _connect(self) -> "pytds.Connection":
        """Opens one blocking TDS connection. Runs off the event loop - call via asyncio.to_thread."""
        params = parse_connection_string(self.connection_string, required=True)
        # Encrypt= / TrustServerCertificate= (and DB_TLS_CA_FILE) turn on TLS - see mssql_tls.
        tls = tls_connect_kwargs(connection_parts(self.connection_string), self.tls_ca_file)
        return pytds.connect(
            server=params["host"],
            port=params["port"] or 1433,
            user=params["user"],
            password=params["password"],
            database=params["database"],
            as_dict=True,
            autocommit=True,
            login_timeout=self.login_timeout,
            **tls,
        )

    async def initialize(self):
        """Opens the fixed-size connection pool."""
        if self._is_initialized:
            logger.warning("MSSQL connection pool already initialized.")
            return
        try:
            pool: asyncio.Queue = asyncio.Queue()
            for _ in range(self.pool_size):
                conn = await asyncio.to_thread(self._connect)
                pool.put_nowait(conn)
            self.pool = pool
            self._is_initialized = True
            logger.info("Successfully established MSSQL connection pool (%d connections).", self.pool_size)
        except pytds.Error as e:
            logger.exception(f"Failed to establish MSSQL connection pool. {e}")
            self._is_initialized = False
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not connect to the database."
            ) from e

    async def reinitialize(self):
        """Re-initializes the connection to the database."""
        logger.info("Re-initializing database connection...")
        await self.close()
        await self.initialize()

    def _ensure_initialized(self):
        """Ensures that the database connection is initialized."""
        if not self._is_initialized or not self.pool:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database connection is not available."
            )

    async def close(self):
        """Closes every pooled connection."""
        if self.pool is not None:
            while not self.pool.empty():
                conn = self.pool.get_nowait()
                await asyncio.to_thread(conn.close)
            self.pool = None
            self._is_initialized = False
            logger.info("MSSQL connection pool closed.")

    @staticmethod
    def _call_procedure_sync(
        conn: "pytds.Connection",
        procedure_name: str,
        parameters: list[Any] | None,
    ) -> list[list[dict]]:
        """Blocking EXEC + result-set collection. Runs via asyncio.to_thread."""
        cursor = conn.cursor()
        try:
            cursor.callproc(procedure_name, tuple(parameters or []))
            all_results: list[list[dict]] = []
            while True:
                try:
                    rows = cursor.fetchall()
                except pytds.ProgrammingError:
                    # This statement in the procedure body (e.g. a plain
                    # UPDATE with no SELECT, like delete_file) produced no
                    # result set at all - not an error, just nothing to read.
                    rows = []
                all_results.append(rows)
                if not cursor.nextset():
                    break
            return all_results
        finally:
            cursor.close()

    async def _replace_connection(self, conn: "pytds.Connection") -> "pytds.Connection":
        """Closes `conn` and opens a fresh one in its place. If the server cannot be reached the
        closed one is returned instead: the pool keeps its size and the next borrower's call
        fails and replaces it, rather than the slot being lost."""
        try:
            await asyncio.to_thread(conn.close)
        except Exception as e:
            logger.warning("Closing a database connection failed: %s", e)
        try:
            return await asyncio.to_thread(self._connect)
        except Exception as e:
            logger.error("Could not replace a database connection: %s", e)
            return conn

    async def _call_procedure_once(
        self, procedure_name: str, parameters: list[Any] | None
    ) -> list[list[dict]]:
        """One attempt: borrow a connection, run the call, always return exactly
        one connection to the pool - the original if it is still healthy, a
        freshly opened one in its place if it is not, so pool size never drifts.

        A cancelled call (client hung up, shutdown) leaves its worker thread still
        running on the connection - asyncio cannot stop a thread - so the connection
        is not handed to the next borrower: it is closed and replaced like a broken one.
        """
        conn = await self.pool.get()
        replace = False
        try:
            return await asyncio.to_thread(
                self._call_procedure_sync, conn, procedure_name, parameters
            )
        except (pytds.OperationalError, asyncio.CancelledError):
            replace = True
            raise
        finally:
            if replace:
                # Shielded so a second cancellation cannot leave the pool a connection short.
                conn = await asyncio.shield(self._replace_connection(conn))
            await self.pool.put(conn)

    async def call_procedure(
        self,
        procedure_name: str,
        parameters: list[Any] | None = None
    ) -> list[list[dict[str, Any]]]:
        """Calls a stored procedure with positional `parameters` in the order it declares
        them; returns one list of row dicts per result set."""
        self._ensure_initialized()
        logger.info(f"Calling stored procedure: {procedure_name}")
        logger.debug(f"Parameters: {parameters}")

        try:
            return await self._call_procedure_once(procedure_name, parameters)
        except pytds.OperationalError as e:
            # _call_procedure_once already replaced the one bad connection with
            # a fresh one before re-raising (see its docstring), so the pool is
            # already healthy - just retry on it. Tearing down and rebuilding
            # the whole pool here, on every single transient error, would both
            # discard pool_size-1 perfectly good connections and - because this
            # is a hand-rolled asyncio.Queue, not a real pool -
            # race any concurrent call's `await self.pool.put(conn)` against
            # `close()`/`initialize()` reassigning self.pool out from under it.
            logger.exception(
                f"Transient database connection error when calling procedure "
                f"'{procedure_name}'; retrying once on the pool's replacement "
                f"connection. Error: {e}"
            )
            return await self._call_procedure_once(procedure_name, parameters)
        except pytds.Error as e:
            logger.exception(f"Database error when calling procedure '{procedure_name}'.")
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while executing a database operation."
            ) from e
