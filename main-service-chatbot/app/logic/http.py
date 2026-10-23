"""What goes over HTTP: comma-separated settings, CORS origins, Server-Sent Event frames, download headers."""
import json
from typing import Any

# Tells nginx (and the OpenShift router) not to buffer the response. Without it a
# reverse proxy holds chunks back and the stream arrives all at once.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def split_csv(value: str | None) -> list[str]:
    """The non-empty, trimmed items of a comma-separated setting."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def cors_origins(local_origins: str | None, extra_origins: str | None) -> list[str]:
    """Every origin allowed to call the API: the local-dev ones, then the deployment's own."""
    return split_csv(local_origins) + split_csv(extra_origins)


def sse_frame(event: str, data: Any) -> str:
    """One Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def attachment_disposition(file_name: str) -> str:
    """A Content-Disposition that makes a browser save the file under its original name instead of rendering it."""
    safe_name = file_name.replace('"', "")
    return f'attachment; filename="{safe_name}"'
