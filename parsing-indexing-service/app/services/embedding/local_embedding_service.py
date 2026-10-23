"""Local embedding backend: a sentence-transformers model loaded from disk.

No network dependency once the model is on disk - the Dockerfile clones it to
<app root>/hf_models at build time. See services/embedding/embedding_service.py for the
contract this implements, and services/embedding/remote_embedding_service.py for the
alternative backend (EMBEDDING_TYPE=remote).
"""
import logging
import os
from pathlib import Path

import numpy as np
from app.logic.embeddings import normalize_texts
from app.services.embedding.embedding_service import EmbeddingService
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# The copy the Dockerfile clones at build time and verifies loads, so it is always
# present in a built image. EMBEDDING_MODEL_PATH overrides it.
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[3] / "hf_models" / "gte-multilingual-base"


class LocalEmbeddingService(EmbeddingService):
    """Loads a Sentence Transformer model lazily, on the first embedding request.
    `device` None lets Sentence Transformers pick a GPU if there is one
    (EMBEDDING_DEVICE); `trust_remote_code` is EMBEDDING_TRUST_REMOTE_CODE."""

    def __init__(
        self, model_path: str | None, batch_size: int, device: str | None = None, trust_remote_code: bool = True
    ):
        self.model_path = model_path or str(DEFAULT_MODEL_PATH)
        self.model_id = os.path.basename(os.path.normpath(self.model_path))
        self.batch_size = batch_size
        self.device = device
        self.trust_remote_code = trust_remote_code
        self.model: SentenceTransformer | None = None

    def _load_model(self) -> None:
        if not os.path.isdir(self.model_path):
            raise FileNotFoundError(f"Model path '{self.model_path}' not found or is not a directory.")
        logger.info("Loading Sentence Transformer model from %s...", self.model_path)
        self.model = SentenceTransformer(self.model_path, device=self.device, trust_remote_code=self.trust_remote_code)
        logger.info("Model loaded onto device: %s", self.model.device)

    def generate_embeddings(self, texts: str | list[str], show_progress_bar: bool = False) -> np.ndarray:
        """1D array for a single string, 2D (one row per text) for a list.

        Raises ValueError for empty or non-string input, TypeError if the model
        failed to load.
        """
        if self.model is None:
            try:
                self._load_model()
            except Exception as e:
                logger.error("Failed to load the embedding model: %s", e)
                raise TypeError("Cannot generate embeddings because the model failed to load.") from e
        texts_to_encode, is_single_input = normalize_texts(texts)
        embeddings = self.model.encode(
            texts_to_encode, batch_size=self.batch_size, show_progress_bar=show_progress_bar, convert_to_numpy=True
        )
        return embeddings[0] if is_single_input else embeddings
