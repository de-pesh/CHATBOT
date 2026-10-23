"""Chooses the relational database backend from configuration.

    `DatabaseService` exposes exactly one operation, `call_procedure`: every read and
    write in this service goes through a **stored procedure**, not SQL built in
    Python. So a new backend is not just a driver - it also needs a dialect port of
    `scripts/mssql/*.sql`. The checklist:

      1. a `DatabaseService` implementation (driver, pool, `call_procedure`
         returning a list of result sets as lists of dicts),
      2. a dialect port of `scripts/mssql/*.sql` in `scripts/<type>/`, plus a target
         class in `services/db/schema_migrator.py` so startup can apply it,
      3. a branch below, and the value added to `Settings.DB_TYPE`.

    Step 2 is the expensive one and there is no way around it short of replacing the
    stored procedures with an ORM or with SQL in the repo.
"""
import logging

from app.config import Settings
from app.services.db.database_service import DatabaseService

logger = logging.getLogger(__name__)


def create_database_service(settings: Settings) -> DatabaseService:
    """Builds the configured database backend. Call `initialize()` on the result."""
    if settings.DB_TYPE == "mssql":
        from app.services.db.mssql_service import MSSQLService

        logger.info("Database backend: MSSQL")
        return MSSQLService(
            settings.DB_CONNECTION_STRING.get_secret_value(), settings.DB_LOGIN_TIMEOUT_SECONDS, settings.MSSQL_POOL_SIZE,
            tls_ca_file=settings.DB_TLS_CA_FILE,
        )
    raise ValueError(f"Unsupported DB_TYPE: {settings.DB_TYPE!r}. Expected: mssql.")
