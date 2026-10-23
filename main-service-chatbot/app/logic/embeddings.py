"""What goes to an embeddings endpoint, what comes back, and when a failure is worth retrying."""
from collections.abc import Iterator, Sequence
from typing import Any


def normalize_texts(texts: str | list[str]) -> tuple[list[str], bool]:
    """Validates `texts` and returns (texts to encode, was a single string). Every
    backend starts with these checks, so they and their messages live here once."""
    if isinstance(texts, str):
        if not texts:
            raise ValueError("Input 'texts' string cannot be empty.")
        return [texts], True
    if isinstance(texts, list):
        if not texts:
            raise ValueError("Input 'texts' list cannot be empty.")
        if not all(isinstance(t, str) for t in texts):
            raise ValueError("All elements in the input list 'texts' must be strings.")
        return texts, False
    raise ValueError("Input 'texts' must be a string or a list of strings.")


def batches(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    """`items` in consecutive slices of `size` (at least 1)."""
    size = max(1, size)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def embeddings_url(base_url: str) -> str:
    """The OpenAI-compatible route under `base_url`."""
    return f"{base_url.rstrip('/')}/embeddings"


def azure_embeddings_url(endpoint: str, deployment: str, api_version: str) -> str:
    """An Azure OpenAI / Foundry deployment's route. `deployment` is the name given
    to the model when it was deployed, not the model's own name."""
    base = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}"
    return f"{embeddings_url(base)}?api-version={api_version}"


def request_body(model: str, batch: Sequence[str], dimensions: int | None) -> dict[str, Any]:
    """The JSON body; `dimensions` is only sent when set, for models that can shorten their output."""
    body: dict[str, Any] = {"model": model, "input": list(batch)}
    if dimensions:
        body["dimensions"] = dimensions
    return body


def parse_status_codes(csv: str) -> frozenset[int]:
    """'429, 503' -> {429, 503}."""
    return frozenset(int(code) for code in csv.split(",") if code.strip())


def backoff_seconds(attempt: int, base: float) -> float:
    """Wait before retry number `attempt + 1` (attempt counts from 0): base ** attempt."""
    return base ** attempt


def vectors_from_response(payload: dict[str, Any] | None) -> list[list[float]]:
    """The embeddings in request order. The API need not return rows in the order
    sent; each row's "index" ties it back to its place in the batch."""
    data = payload.get("data") if payload else None
    if not data:
        raise ValueError(f"Embedding endpoint response had no 'data': {payload}")
    return [item["embedding"] for item in sorted(data, key=lambda item: item.get("index", 0))]
