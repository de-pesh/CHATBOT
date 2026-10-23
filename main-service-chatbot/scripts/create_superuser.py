"""Creates or updates one row in dbo.users - the account needed to log in once
DISABLE_AUTH is set False (see app/config.py: Settings.DISABLE_AUTH).

Idempotent by email: run it again for the same --email to reset a password or
change its role, instead of accumulating duplicate accounts. There is no
self-registration endpoint (POST /auth/authenticate only logs in an existing
row), so this script is the only way to create the first one.

    cd main-service-chatbot
    poetry run python scripts/create_superuser.py --email admin@example.com --first-name Ada \
        --last-name Lovelace --password 'change-me' --role admin

Reads only DB_CONNECTION_STRING and the two pool settings - from the
environment, or the same repo-root .env the service reads - same as
scripts/apply_schema.py, and for the same reason: this runs standalone,
without the rest of Settings (LLM keys, storage, queue) configured.
"""
import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

import bcrypt

SERVICE_ROOT = Path(__file__).resolve().parents[1]
# config.py finds ../.env relative to the working directory.
os.chdir(SERVICE_ROOT)
sys.path.insert(0, str(SERVICE_ROOT))

from app.models.auth import Role  # noqa: E402
from app.services.db.mssql_service import MSSQLService  # noqa: E402
from app.services.db.stored_procedures import StoredProcedures  # noqa: E402
from pydantic import SecretStr  # noqa: E402
from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402

ROLES = list(Role.__args__)  # "admin", "user" - see app/models/auth.py


class DbSettings(BaseSettings):
    """Just the settings this script needs, so it runs without the rest of the config."""

    DB_CONNECTION_STRING: SecretStr
    # Same defaults as the service's Settings - see app/config.py.
    DB_LOGIN_TIMEOUT_SECONDS: int = 10
    MSSQL_POOL_SIZE: int = 1  # one connection is enough for a one-off script
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")


def hash_password(plain: str) -> str:
    """The same two calls as app.security.get_password_hash, duplicated rather than imported so
    this script does not need the rest of Settings (SECRET_KEY, LLM keys, ...) just to hash a
    password; app.security.verify_password (plain bcrypt.checkpw) is what /auth/authenticate
    checks it against, so any bcrypt hash works here regardless of cost factor."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def upsert_superuser(args: argparse.Namespace) -> int:
    settings = DbSettings()
    db = MSSQLService(
        settings.DB_CONNECTION_STRING.get_secret_value(), settings.DB_LOGIN_TIMEOUT_SECONDS, settings.MSSQL_POOL_SIZE,
    )
    await db.initialize()
    try:
        password = args.password or getpass.getpass("Password: ")
        if not password:
            raise SystemExit("A password is required (--password or the prompt).")

        actor = "create_superuser.py"
        existing = (await db.call_procedure(StoredProcedures.FILTER_USERS, [None, None, None, args.email, None, None, None, None]))[0]
        user_id = existing[0]["id"] if existing else None

        result = await db.call_procedure(
            StoredProcedures.UPSERT_USER,
            [
                user_id, args.first_name, args.last_name, args.email, hash_password(password),
                args.role, not args.inactive, actor,
            ],
        )
        new_id = result[0][0]["id"]
        verb = "Updated" if user_id else "Created"
        print(f"{verb} user {new_id} ({args.email}), role={args.role!r}.")
        return 0
    finally:
        await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--first-name", required=True)
    parser.add_argument("--last-name", required=True)
    parser.add_argument("--password", help="Omit to be prompted (not echoed) instead of putting it in shell history.")
    parser.add_argument(
        "--role", choices=ROLES, default="admin",
        help="'admin' can upload/delete files and run ingestion; 'user' can only chat. Default: admin.",
    )
    parser.add_argument("--inactive", action="store_true", help="Create/update the row as disabled (is_active=0).")
    args = parser.parse_args()

    return asyncio.run(upsert_superuser(args))


if __name__ == "__main__":
    sys.exit(main())
