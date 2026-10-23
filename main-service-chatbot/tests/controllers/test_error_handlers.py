"""The app-wide exception handlers keep what an HTTPException says, headers included."""
import asyncio
from types import SimpleNamespace

from app.controllers.error_controller import http_exception_handler
from fastapi import HTTPException


def _handle(exc):
    request = SimpleNamespace(url=SimpleNamespace(path="/chat"), method="POST")
    return asyncio.run(http_exception_handler(request, exc))


def test_a_429_keeps_its_retry_after_header():
    response = _handle(HTTPException(429, "Too many messages; try again in 7 seconds.", headers={"Retry-After": "7"}))
    assert response.status_code == 429 and response.headers["retry-after"] == "7"


def test_an_exception_without_headers_is_unchanged():
    response = _handle(HTTPException(404, "missing"))
    assert response.status_code == 404 and "retry-after" not in response.headers
