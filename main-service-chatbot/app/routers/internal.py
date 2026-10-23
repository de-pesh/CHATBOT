"""Internal endpoints called by other services, not by the browser.

These are not authenticated: they are only reachable from within the cluster
(the parsing-indexing-service worker calls /internal/catalog/refresh after
each successful ingest). Do not expose them on a public route.
"""
import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/catalog/refresh")
async def catalog_refresh(request: Request):
    """Ask main-service to refresh the knowledge-base catalog descriptions.

    Fire-and-forget: the worker does not wait for the refresh to complete.
    Returns immediately so the worker's HTTP call does not block.
    """
    catalog = getattr(request.app.state, "kb_catalog", None)
    if catalog is None:
        logger.warning("[internal] catalog/refresh called but no catalog is running.")
        return {"ok": False, "reason": "no catalog"}
    import asyncio
    asyncio.create_task(catalog.refresh())
    return {"ok": True}
