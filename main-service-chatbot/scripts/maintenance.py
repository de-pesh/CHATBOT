"""One-off maintenance jobs, run inside the main-service pod (or anywhere with the same environment):

    oc exec -n ai-common deploy/main-service -- python scripts/maintenance.py versions-sync
    oc exec -n ai-common deploy/main-service -- python scripts/maintenance.py backfill-hashes
    oc exec -n ai-common deploy/main-service -- python scripts/maintenance.py purge

versions-sync     Gives files uploaded before document versions their key and version number (oldest upload first), elects
                  each document's latest version and writes it onto the stored chunks. Run once after upgrading.
backfill-hashes   Hashes files uploaded before duplicate detection, so re-uploading one is refused as a duplicate.
purge             Applies AUDIT_RETENTION_DAYS / TRACE_RETENTION_DAYS now.

These are the same operations as POST /admin/versions/sync, /admin/files/backfill-hashes and /admin/purge, for when you
would rather not sign in to call them. Safe to run again. Prints what it did as JSON.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
os.chdir(SERVICE_ROOT)
sys.path.insert(0, str(SERVICE_ROOT))

from app.config import get_settings  # noqa: E402
from app.controllers.hash_backfill import backfill_content_hashes  # noqa: E402
from app.controllers.version_controller import sync_all  # noqa: E402
from app.routers.admin import purge_old_rows  # noqa: E402
from app.services.db.database_factory import create_database_service  # noqa: E402
from app.services.storage.storage_factory import create_storage_service  # noqa: E402

JOBS = ("versions-sync", "backfill-hashes", "purge")


async def run(job: str) -> dict:
    settings = get_settings()
    db = create_database_service(settings)
    await db.initialize()
    try:
        if job == "versions-sync":
            return await sync_all(db, settings)
        if job == "purge":
            return await purge_old_rows(db, settings)
        storage = create_storage_service(settings)
        await storage.initialize()
        try:
            return await backfill_content_hashes(db, storage)
        finally:
            await storage.close()
    finally:
        await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("job", choices=JOBS)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.job)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
