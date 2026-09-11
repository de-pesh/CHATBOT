import logging
from langchain_text_splitters import RecursiveCharacterTextSplitter
from typing import Dict, List, Union, Any

from app.services.embedding_service import EmbeddingService

# Define the special delimiter for policy documents
POLICY_PAGE_DELIMITER = f"\n\n{'-'*50}\n\n"

def prepare_document(
    input_text: str,
    embedding_service: EmbeddingService,
    is_policy: bool = False,
    chunk_size: int = 4000,
    chunk_overlap: int = 200,
) -> List[Dict[str, Union[str, List[float], Dict[str, Any]]]]:
    """
    Splits input text into chunks, adds metadata, generates embeddings,
    and returns a list of dictionaries.

    If is_policy is True, it splits the document by a special page delimiter,
    chunks each page, and adds the 'page_number' to each chunk's metadata.
    If is_policy is False, it chunks the entire document and does not add a page number.

    Args:
        input_text (str): The text content to split and embed.
        embedding_service (EmbeddingService): A class to handle embedding.
        is_policy (bool, optional): If True, use the policy-based chunking strategy.
                                    Defaults to False.
        chunk_size (int, optional): The maximum size of each text chunk. Defaults to 4000.
        chunk_overlap (int, optional): The number of characters to overlap. Defaults to 200.

    Returns:
        List[Dict[str, Union[str, List[float], Dict[str, Any]]]]: A list of dictionaries
        for each chunk, including textContent, vectorContent, and metadata.
    """
    # 1. Input Validation (remains the same)
    if not input_text or not isinstance(input_text, str):
        raise ValueError("input_text must be a non-empty string.")
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer.")
    if not isinstance(chunk_overlap, int) or chunk_overlap < 0:
        raise ValueError("chunk_overlap must be a non-negative integer.")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size.")

    logging.info(f"Starting chunking and embedding process. is_policy={is_policy}")
    logging.info(f"Chunk size: {chunk_size}, Chunk overlap: {chunk_overlap}")

    # 2. Text Splitting with conditional metadata
    chunks_with_metadata = []
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )

    try:
        if is_policy:
            logging.info("Using policy-based chunking. Adding page numbers to metadata.")
            pages = input_text.split(POLICY_PAGE_DELIMITER)
            logging.info(f"Split document into {len(pages)} pages using policy delimiter.")
            
            for page_index, page_content in enumerate(pages):
                page_number = page_index + 1 # Page numbers start from 1
                if page_content.strip():
                    chunks_from_page = text_splitter.split_text(page_content)
                    for chunk in chunks_from_page:
                        # Create metadata WITH page number
                        chunks_with_metadata.append({
                            "text": chunk,
                            "metadata": {"page_number": page_number}
                        })
        else:
            logging.info("Using standard chunking. No page numbers will be added.")
            text_chunks = text_splitter.split_text(input_text)
            for chunk in text_chunks:
                # MODIFIED: Create EMPTY metadata dictionary when not a policy
                chunks_with_metadata.append({
                    "text": chunk,
                    "metadata": {} 
                })

        logging.info(f"Total chunks with metadata created: {len(chunks_with_metadata)}")
        if not chunks_with_metadata:
            logging.warning("Text splitting resulted in zero chunks.")
            return []

    except Exception as e:
        logging.error(f"Error during text splitting: {e}", exc_info=True)
        raise

    # 3. Generate Embeddings (remains the same)
    try:
        text_for_embedding = [item['text'] for item in chunks_with_metadata]
        
        logging.info(f"Calling embedding service for {len(text_for_embedding)} chunks...")
        embeddings = embedding_service.generate_embeddings(text_for_embedding)
        logging.info(f"Embeddings received. Shape: {embeddings.shape}")

        if embeddings.ndim != 2 or embeddings.shape[0] != len(text_for_embedding):
            raise ValueError(f"Embedding shape mismatch. Expected ({len(text_for_embedding)}, vector_dim), "
                             f"but got {embeddings.shape}.")

    except Exception as e:
        logging.error(f"Error during embedding generation: {e}", exc_info=True)
        raise

    # 4. Prepare Output Dictionary List (remains the same)
    results = []
    for i, chunk_data in enumerate(chunks_with_metadata):
        results.append({
            "textContent": chunk_data['text'],
            "vectorContent": embeddings[i].tolist(),
            "metadata": chunk_data['metadata'] # This will have page_number or be empty
        })

    logging.info(f"Prepared {len(results)} chunk dictionaries with text, vectors, and metadata.")
    return results
