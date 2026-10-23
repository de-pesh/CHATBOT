"""Remote embedding backend: any OpenAI-compatible /embeddings endpoint.

Used when EMBEDDING_TYPE=litellm or remote (they differ only in name). Same
base_url/model_name/api_key shape as the OLLAMA_* settings in config.py, so
this is a config change away from pointing at:

  * a LiteLLM proxy (EMBEDDING_BASE_URL="http://<host>:4000",
    EMBEDDING_MODEL_NAME=the alias configured on the proxy - the proxy is what
    fans out to Azure, Bedrock, Vertex, Ollama and the rest),
  * a real Ollama server's OpenAI-compatible endpoint
    (EMBEDDING_BASE_URL="http://<host>:11434/v1", EMBEDDING_MODEL_NAME=
    e.g. "nomic-embed-text"),
  * or any other self-hosted / gateway embeddings server that speaks the same
    {"model", "input"} in / {"data": [{"embedding": [...]}]} out shape.

Uses urllib from the standard library rather than adding an HTTP client
dependency, and is synchronous by design (see the contract in
embedding_service.py: both call sites already run generate_embeddings inside
asyncio.to_thread). What goes on the wire, and the retry arithmetic, is in
logic/embeddings.py.
"""
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

import numpy as np
from app.logic.embeddings import (
    backoff_seconds,
    batches,
    normalize_texts,
    request_body,
    vectors_from_response,
)
from app.services.embedding.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class RemoteEmbeddingService(EmbeddingService):
    """Calls an OpenAI-compatible embeddings endpoint over HTTP. The tuning
    arguments are the EMBEDDING_* settings of config.py."""

    def __init__(
        self,
        endpoint: str,
        model_name: str,
        api_key: str | None,
        batch_size: int,
        dimensions: int | None,
        timeout: float,
        max_retries: int,
        backoff_base: float,
        retry_statuses: frozenset[int],
    ):
        self.endpoint = endpoint
        self.model_id = model_name
        self.api_key = api_key
        # A document is hundreds of chunks and endpoints cap the inputs per request.
        self.batch_size = batch_size
        # Sent as `dimensions` when set, so a model that can shorten its output
        # (OpenAI's text-embedding-3-*) matches EMBEDDING_DIMENSION.
        self.dimensions = dimensions
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.retry_statuses = retry_statuses

    def generate_embeddings(
        self,
        texts: str | list[str],
        show_progress_bar: bool = False,  # interface parity with the local backend; nothing to show here
    ) -> np.ndarray:
        texts_to_encode, is_single_input = normalize_texts(texts)
        vectors = [
            vector
            for batch in batches(texts_to_encode, self.batch_size)
            for vector in vectors_from_response(self._post(request_body(self.model_id, batch, self.dimensions)))
        ]
        embeddings = np.array(vectors, dtype=np.float32)
        if embeddings.shape[0] != len(texts_to_encode):
            raise ValueError(f"Expected {len(texts_to_encode)} embeddings, got {embeddings.shape[0]}.")
        return embeddings[0] if is_single_input else embeddings

    def _auth_headers(self) -> dict[str, str]:
        """How the key is presented. A service that wants a different header
        (Azure's `api-key`) overrides this and nothing else."""
        return {"Authorization": f"Bearer {self.api_key}"}

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """One POST, retried on a status in retry_statuses or a network error, with
        exponential backoff."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers.update(self._auth_headers())
        data = json.dumps(body).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(self.endpoint, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.URLError as e:  # HTTPError is a URLError
                if isinstance(e, urllib.error.HTTPError):
                    retryable = e.code in self.retry_statuses
                    problem = f"Embedding endpoint returned {e.code}: {e.read().decode('utf-8', errors='replace')}"
                else:
                    retryable = True
                    problem = f"Could not reach embedding endpoint {self.endpoint}: {e}"
                if not retryable or attempt >= self.max_retries:
                    raise RuntimeError(problem) from e
                logger.warning("%s; retrying (%d/%d).", problem, attempt + 1, self.max_retries)
                time.sleep(backoff_seconds(attempt, self.backoff_base))
