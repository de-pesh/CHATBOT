"""Chooses the relational database backend from configuration.

    IMPORTANT - what is and is not portable here.

    `DatabaseService` exposes exactly one operation: `call_procedure`. Every
    read and write in this service goes through a **stored procedure**, not
    through SQL built in Python. That means swapping DB_TYPE moves the driver
    and the connection handling, but it does **not** move the data layer: the
    nine procedures in `scripts/*.sql` are MySQL syntax and have to be ported
    to the target dialect before another backend can serve a single request.

    So the checklist for adding a backend is:

      1. a `DatabaseService` implementation (driver, pool, `call_procedure`
         returning a list of result sets as lists of dicts),
      2. a dialect port of `scripts/*.sql`, kept next to the MySQL originals,
      3. a branch below.

    Step 2 is the expensive one and there is no way around it short of
    replacing the stored procedures with an ORM or with SQL in the repo. That
    would be the real fix if multi-backend support becomes a requirement
    rather than an option; it is a much larger change than this file.
"""
import logging

from app.config import Settings
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)


def create_database_service(settings: Settings) -> DatabaseService:
    """Builds the configured database backend. Call `initialize()` on the result."""
    db_type = (settings.DB_TYPE or "").lower()
    connection_string = settings.DB_CONNECTION_STRING.get_secret_value()

    if db_type == "mysql":
        from app.services.mysql_service import MySQLService

        logger.info("Database backend: MySQL")
        return MySQLService(connection_string)

    if db_type in ("mssql", "postgres"):
        raise NotImplementedError(
            f"DB_TYPE={db_type!r} has no implementation yet. It needs a DatabaseService "
            f"subclass and a {db_type} port of main-service/scripts/*.sql - the data "
            f"layer is stored procedures, so the connection string alone will not move "
            f"it. See the module docstring in services/database_factory.py."
        )

    raise ValueError(
        f"Unsupported DB_TYPE: {settings.DB_TYPE!r}. Expected one of: mysql, mssql, postgres."
    )
