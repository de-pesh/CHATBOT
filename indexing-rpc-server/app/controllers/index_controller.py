from io import BytesIO
import logging
import uuid
from typing import Optional
from app.services.azure_blob_storage_service import AzureBlobService
from app.services.embedding_service import EmbeddingService
from app.services.mongo_service import MongoEmbeddingStore
from app.utils.document_utils import prepare_document

logger = logging.getLogger(__name__)

async def index_handler(
        file_path: str,
        collection_name: str,
        metadata: dict,
        azure_blob_service: AzureBlobService,
        mongo_service: MongoEmbeddingStore,
        embedding_service: EmbeddingService,
        is_policy: bool = False,
        source_file_id: Optional[str] = None
) -> str:
    """
    Handles the indexing process for a file.

    It prepares document chunks using prepare_document and then enriches
    their metadata before inserting them into MongoDB. The use of 'is_policy'
    determines if page numbers are included in the chunk metadata.

    Args:
        file_path (str): The path to the blob file in Azure Storage.
        collection_name (str): The MongoDB collection name.
        metadata (dict): Base metadata for the document.
        azure_blob_service (AzureBlobService): Azure Blob Service client.
        mongo_service (MongoEmbeddingStore): MongoDB client.
        embedding_service (EmbeddingService): Embedding service client.
        is_policy (bool, optional): Flag for policy-based chunking. Defaults to False.
        source_file_id (Optional[str], optional): Existing source file ID. Defaults to None.

    Returns:
        str: The UUID representing the source file that was processed.
    """
    logger.info(f"Starting index_handler for collection: {collection_name}, file: {file_path}")

    if source_file_id:
        logger.info(f"Using provided source_file_id: {source_file_id}")
    else:
        source_file_id = str(uuid.uuid4())
        logger.info(f"Generated new source_file_id: {source_file_id}")

    # --- Step 1: Download file from Azure Blob Storage ---
    try:
        file_stream: BytesIO = await azure_blob_service.download_blob_stream(blob_path=file_path)
        text_data = file_stream.read().decode("utf-8")
        logger.info(f"Successfully downloaded and decoded blob: {file_path}")
    except Exception as e:
        logger.error(f"Failed to download/decode blob {file_path}: {e}", exc_info=True)
        raise
    finally:
        if 'file_stream' in locals() and file_stream:
            file_stream.close()

    # --- Step 2: Prepare document data ---
    try:
        prepared_data = prepare_document(
            input_text=text_data,
            embedding_service=embedding_service,
            is_policy=is_policy
        )

        # MODIFIED: This loop now correctly merges metadata for both cases.
        for data_chunk in prepared_data:
            # Create a dictionary of the additional metadata to add
            additional_metadata = metadata.copy()
            additional_metadata['uuid'] = str(uuid.uuid4())
            additional_metadata['source_file_id'] = source_file_id
            additional_metadata['original_file_path'] = file_path
            additional_metadata['isactive'] = True

            # Use .update() to merge the additional metadata into the
            # existing metadata dictionary from the chunk. This preserves
            # 'page_number' if it exists.
            data_chunk["metadata"].update(additional_metadata)
        
        logger.info(f"Prepared and enriched metadata for {len(prepared_data)} document chunks.")
    except Exception as e:
        logger.error(f"Failed to prepare document data for {file_path}: {e}", exc_info=True)
        raise

    if not prepared_data:
        logger.warning(f"No data prepared from {file_path}. Nothing to index.")
        return source_file_id

    # --- Step 3: Interact with MongoDB ---
    try:
        inserted_ids, inserted_count = await mongo_service.insert_data(
            collection_name=collection_name,
            data_items=prepared_data
        )
        if inserted_ids is None or inserted_count == 0:
            raise RuntimeError(f"Failed to insert chunks into MongoDB collection '{collection_name}'.")
        logger.info(f"Successfully inserted {inserted_count} items into '{collection_name}'.")

        index_created = await mongo_service.create_vector_index(collection_name=collection_name)
        if not index_created:
            raise RuntimeError(f"Failed to ensure vector index for collection '{collection_name}'.")
        logger.info(f"Vector index ensured for collection: {collection_name}")

    except Exception as e:
        logger.error(f"MongoDB operation failed for collection {collection_name}: {e}", exc_info=True)
        raise
    
    return source_file_id
