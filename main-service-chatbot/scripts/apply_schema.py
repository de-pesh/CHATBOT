"""Creates the database if it is missing and applies scripts/mssql/*.sql to it.

The service does this itself at startup (AUTO_APPLY_SCHEMA, on by default), so
this command is for doing it ahead of time or inspecting it: the docker-compose
`db-schema` service and `python local.py db` run it. The work is in
app/services/db/schema_migrator.py, which the service shares. It reads only
DB_CONNECTION_STRING and the two timeouts (DB_LOGIN_TIMEOUT_SECONDS,
SCHEMA_LOCK_TIMEOUT_SECONDS) - from the environment, or the same repo-root .env
the service reads - so it needs nothing else configured and targets exactly what
the service will.

    cd main-service-chatbot
    poetry run python scripts/apply_schema.py            # apply what is pending
    poetry run python scripts/apply_schema.py --status   # show applied / pending
    poetry run python scripts/apply_schema.py --force    # re-run every script
    poetry run python scripts/apply_schema.py --baseline # record all as applied, run none

Applied scripts are recorded in a `schema_migrations` table so a re-run only
applies what is new. --force is a repair tool, not the normal path. For a
database that already has this schema, `--baseline` records everything as
applied without running it.
"""
import argparse
import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
# config.py finds ../.env relative to the working directory.
os.chdir(SERVICE_ROOT)
sys.path.insert(0, str(SERVICE_ROOT))

from app.services.db.schema_migrator import (  # noqa: E402
    SchemaError,
    list_scripts,
    open_target,
    run_scripts,
)
from pydantic import SecretStr  # noqa: E402
from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402


class DbSettings(BaseSettings):
    """Just the two settings this script needs, so it runs without the rest of the config."""

    DB_CONNECTION_STRING: SecretStr
    # Same meaning and defaults as the service's Settings - see app/config.py.
    DB_LOGIN_TIMEOUT_SECONDS: int = 10
    SCHEMA_LOCK_TIMEOUT_SECONDS: int = 120
    DB_TLS_CA_FILE: str | None = None
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--status", action="store_true", help="list applied and pending scripts, without applying any "
                        "(still creates the empty database and tracking table if absent)")
    parser.add_argument("--force", action="store_true", help="run every script again, applied or not")
    parser.add_argument("--baseline", action="store_true",
                        help="record every script as applied without running it - for a database "
                             "that already has this schema")
    args = parser.parse_args()

    settings = DbSettings()
    scripts = list_scripts()
    try:
        target, _, where = open_target(
            settings.DB_CONNECTION_STRING.get_secret_value(),
            settings.DB_LOGIN_TIMEOUT_SECONDS, settings.SCHEMA_LOCK_TIMEOUT_SECONDS, settings.DB_TLS_CA_FILE,
        )
    except SchemaError as exc:
        print(exc)
        return 1

    try:
        target.lock()
        try:
            done = target.applied()
            pending = [s for s in scripts if args.force or s.name not in done]
            print(where)
            for script in scripts:
                print(f"  {'applied' if script.name in done else 'pending':8} {script.name}")
            if args.status:
                return 0
            if args.baseline:
                for script in scripts:
                    target.record(script.name)
                print(f"Recorded {len(scripts)} script(s) as applied; nothing was run.")
                return 0
            if not pending:
                print("Nothing to apply.")
                return 0
            try:
                run_scripts(target, pending, lambda line: print(line, flush=True))
            except SchemaError as exc:
                print(exc)
                return 1
            print("Schema is up to date.")
            return 0
        finally:
            target.unlock()
    except SchemaError as exc:  # the lock timed out
        print(exc)
        return 1
    finally:
        target.close()


if __name__ == "__main__":
    sys.exit(main())
