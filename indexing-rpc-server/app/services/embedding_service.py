import os
import logging
from pathlib import Path
from typing import List, Optional, Union, overload
import numpy as np
from sentence_transformers import SentenceTransformer, __version__ as st_version

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def _resolve_default_model_path() -> str:
    """Resolve the default model path relative to this file's location.

    The Dockerfile clones the model to <app root>/hf_models and verifies it
    loads at build time, so the path is always present in a built image.
    """
    return str(Path(__file__).resolve().parents[2] / "hf_models" / "gte-multilingual-base")


class EmbeddingService:
    """
    A service class for loading a Sentence Transformer model and generating embeddings.

    Attributes:
        model_path (str): Path to the directory containing the Sentence Transformer model.
        batch_size (int): Batch size to use for encoding.
        device (Optional[str]): Device to run the model on (e.g., 'cpu', 'cuda').
                                If None, Sentence Transformers decides automatically.
        model (Optional[SentenceTransformer]): The loaded Sentence Transformer model instance.
                                                Loaded lazily on the first call to generate_embeddings.
    """
    def __init__(
            self,
            model_path: Optional[str] = None,
            batch_size: int = 32,
            device: Optional[str] = None
    ):
        """
        Initializes the EmbeddingService.

        Args:
            model_path (Optional[str]): The file path to the directory containing the
                              downloaded Sentence Transformers model files. If None,
                              resolved automatically relative to this file.
            batch_size (int): The batch size to use for encoding. Defaults to 32.
            device (Optional[str]): The device (e.g., 'cpu', 'cuda', 'cuda:0')
                                    to load the model onto. If None, Sentence
                                    Transformers will attempt to use a GPU if available.
                                    Defaults to None.
        """
        if model_path is None:
            model_path = _resolve_default_model_path()
        if not isinstance(model_path, str) or not model_path:
             raise ValueError("model_path must be a non-empty string.")
        if not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer.")

        self.model_path = model_path
        self.batch_size = batch_size
        self.device = device
        self.model: Optional[SentenceTransformer] = None # Initialize model as None for lazy loading
        logging.info(f"EmbeddingService initialized with model_path='{self.model_path}', batch_size={self.batch_size}, device='{self.device or 'auto'}'")
        logging.info(f"Using sentence-transformers version: {st_version}")


    def _load_model_internal(self):
        """Loads the Sentence Transformer model from the specified path."""
        if self.model is not None:
            logging.info("Model already loaded.")
            return

        # Validate Model Path just before loading
        if not os.path.isdir(self.model_path):
            error_msg = f"Model path '{self.model_path}' not found or is not a directory."
            logging.error(error_msg)
            raise FileNotFoundError(error_msg)
        logging.info(f"Found model directory at: {self.model_path}")

        # Load the Local Model
        try:
            logging.info(f"Loading Sentence Transformer model from local path: {self.model_path}...")
            # trust_remote_code=True might be necessary for some models, keep it if needed
            self.model = SentenceTransformer(self.model_path, device=self.device, trust_remote_code=True)
            logging.info(f"Model loaded successfully onto device: {self.model.device}")
        except Exception as e:
            logging.error(f"Failed to load model from '{self.model_path}': {e}", exc_info=True)
            # Reset model to None if loading failed
            self.model = None
            raise  # Re-raise the exception after logging


    def ensure_model_loaded(self):
        """Public method to explicitly load the model if needed."""
        if self.model is None:
            self._load_model_internal()
        else:
             logging.info("Model is already loaded.")

    @overload
    def generate_embeddings(
        self,
        texts: str,
        show_progress_bar: bool = False
    ) -> np.ndarray: ...

    @overload
    def generate_embeddings(
        self,
        texts: List[str],
        show_progress_bar: bool = False
    ) -> np.ndarray: ...

    def generate_embeddings(
            self,
            texts: Union[str, List[str]],
            show_progress_bar: bool = False
    ) -> np.ndarray:
        """
        Generates embeddings for the given text(s).

        Loads the model lazily on the first call if it hasn't been loaded yet.

        Args:
            texts (Union[str, List[str]]): A single string or a list of strings
                                        for which to generate embeddings.
            show_progress_bar (bool, optional): Whether to show a progress bar
                                                during encoding. Defaults to False.

        Returns:
            np.ndarray: A numpy array containing the embeddings.
                        - If the input `texts` was a single string, returns a 1D array.
                        - If the input `texts` was a list of strings, returns a 2D array.

        Raises:
            FileNotFoundError: If the model_path does not exist or is not a directory
                               (raised during lazy loading).
            ValueError: If the input `texts` is empty or not a string/list of strings.
            TypeError: If the model failed to load previously.
            Exception: If there's an error during encoding.
        """
        # 1. Ensure model is loaded (Lazy Loading)
        if self.model is None:
            logging.info("Model not loaded yet. Triggering lazy loading...")
            try:
                self._load_model_internal()
            except Exception as e:
                # Provide a clear error if embedding generation is attempted after load failure
                raise TypeError("Cannot generate embeddings because the model failed to load.") from e

        # Should not happen if _load_model_internal raises correctly, but defensive check
        if self.model is None:
            raise TypeError("Model is not loaded, cannot generate embeddings.")

        # 2. Validate and prepare input texts
        if isinstance(texts, str):
            if not texts:
                raise ValueError("Input 'texts' string cannot be empty.")
            logging.debug("Received single string input.")
            texts_to_encode = [texts]
            is_single_input = True
        elif isinstance(texts, list):
            if not texts:
                raise ValueError("Input 'texts' list cannot be empty.")
            if not all(isinstance(t, str) for t in texts):
                 raise ValueError("All elements in the input list 'texts' must be strings.")
            logging.debug(f"Received list of {len(texts)} strings.")
            texts_to_encode = texts
            is_single_input = False
        else:
            raise ValueError("Input 'texts' must be a string or a list of strings.")

        # 3. Generate Embeddings
        try:
            logging.info(f"Generating embeddings for {len(texts_to_encode)} text(s) using batch size {self.batch_size}...")
            embeddings = self.model.encode(
                texts_to_encode,
                batch_size=self.batch_size,
                show_progress_bar=show_progress_bar,
                convert_to_numpy=True
            )
            logging.info(f"Embeddings generated successfully. Shape: {embeddings.shape}")

            # 4. Adjust return format based on input type
            return embeddings[0] if is_single_input else embeddings

        except Exception as e:
            logging.error(f"Failed to generate embeddings: {e}", exc_info=True)
            raise # Re-raise the exception after logging