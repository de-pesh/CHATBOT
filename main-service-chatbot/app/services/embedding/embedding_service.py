"""Backend-neutral embedding interface.

Mirrors services/vector/vector_store.py: one small contract, chosen by
EMBEDDING_TYPE via services/embedding/embedding_factory.py, so nothing outside
this file and the factory names a concrete embedding backend.

    generate_embeddings   text(s) -> vector(s), as a numpy array

Synchronous by design: the one call site (chat_context.fetch_context) already
runs this inside asyncio.to_thread - a remote backend's HTTP call is a plain
blocking request, so it needs no async path of its own. Implementations:
remote_embedding_service.py (litellm, remote), azure_embedding_service.py
(azure). EMBEDDING_TYPE=local (a local sentence-transformers model, needing
torch) is deliberately not ported here - see embedding_factory.py.

Whatever implementation is configured, its output dimension must match
EMBEDDING_DIMENSION (see config.py) - the vector store's index is created
with that fixed size.
"""
from abc import ABC, abstractmethod

import numpy as np


class EmbeddingService(ABC):
    """Contract every embedding backend implements."""

    # Names the model behind the vectors. Stored on every chunk as
    # `embeddingModel`, so a collection can tell which model produced which
    # vectors and tools/reindex.py knows what still needs re-embedding.
    model_id: str = "unknown"

    @abstractmethod
    def generate_embeddings(
        self,
        texts: str | list[str],
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        """Embeds text(s).

        Args:
            texts: A single string or a list of strings.
            show_progress_bar: Best-effort progress display; implementations
                that cannot show one (e.g. a single HTTP call) may ignore it.

        Returns:
            np.ndarray: 1D if `texts` was a single string, 2D (one row per
            input) if `texts` was a list.
        """
        raise NotImplementedError
