import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import motor.motor_asyncio
from pymongo.errors import ConnectionFailure, OperationFailure
from pymongo.operations import SearchIndexModel
from pymongo.results import InsertManyResult, UpdateResult

class MongoEmbeddingStore:
    """
    A class to manage storing and searching embeddings in MongoDB using Motor, with logging.
    """

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        default_collection_name: str,
    ):
        """
        Initializes the MongoEmbeddingStore.

        Args:
            mongo_uri: The MongoDB connection string.
            db_name: The name of the database to use.
            default_collection_name: The default collection name to use if not
                                    specified in methods.
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.default_collection_name = default_collection_name
        self._client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
        self.db: Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None
        self.VECTOR_FIELD: str = "vectorContent"
        self.EXPECTED_DIMENSION = 768
        self.INDEX_NAME = "vector_index"
        logging.info(f"MongoEmbeddingStore initialized for db: '{self.db_name}'")

        self.clean_connection_string()


    def clean_connection_string(self):
        """
        Replaces all '@' characters in the input string with '%40', except the last one,
        using regular expressions.

        Returns:
            str: The modified string with all '@' replaced by '%40' except the last one.
        """
        if self.mongo_uri.count('@') > 1:
            logging.info("Found extra @ cleaning mongo connection string.")
    
            matches = list(re.finditer(r'@', self.mongo_uri))

            # Get the positions of all but the last '@'
            indices_to_replace = [m.start() for m in matches[:-1]]

            # Replace them manually using a list of characters
            s_list = list(self.mongo_uri)
            for idx in indices_to_replace:
                s_list[idx] = '%40'
            
            self.mongo_uri = ''.join(s_list)


    async def connect(self) -> bool:
        """
        Establishes the asynchronous connection to MongoDB.

        Returns:
            True if connection is successful, False otherwise.
        """
        if self.db is not None:
            logging.info("Already connected to MongoDB.")
            return True
        try:
            #match = re.search(r'@([^/]+)', self.mongo_uri)
            log_uri = self.mongo_uri
            logging.info(f"Attempting to connect to MongoDB at {log_uri}...")
            self._client = motor.motor_asyncio.AsyncIOMotorClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=5000
            )
            # The ismaster command is cheap and does not require auth.
            await self._client.admin.command('ismaster')
            self.db = self._client[self.db_name]
            logging.info(f"Successfully connected to MongoDB, using DB: '{log_uri}'")
            return True
        except ConnectionFailure as e:
            logging.error(f"Failed to connect to MongoDB at {log_uri}. Details: {e}", exc_info=True)
            self._client = None
            self.db = None
            return False
        except Exception as e:
            # Catch other potential errors during client setup or initial command
            logging.error(f"An unexpected error occurred during MongoDB connection: {e}", exc_info=True)
            self._client = None
            self.db = None
            return False


    def close(self):
        """
        Closes the connection to MongoDB.
        """
        if self._client:
            logging.info("Closing MongoDB connection.")
            self._client.close()
            self._client = None
            self.db = None
        else:
            logging.info("No active MongoDB connection to close.")


    async def _get_collection(
        self, collection_name: Optional[str] = None
    ) -> Optional[motor.motor_asyncio.AsyncIOMotorCollection]:
        """
        Gets the collection object, ensuring the database connection is active.
        MongoDB automatically creates the collection on first write if it doesn't exist.

        Args:
            collection_name: The specific collection name. Uses default if None.

        Returns:
            An AsyncIOMotorCollection object or None if not connected.
        """
        if self.db is None:
            logging.error("Cannot get collection: Not connected to MongoDB. Call connect() first.")
            return None
        target_collection_name = collection_name or self.default_collection_name
        return self.db[target_collection_name]


    async def create_vector_index(
        self,
        collection_name: Optional[str] = None,
    ) -> bool:
        """
        Creates a MongoDB Atlas Vector Search index on the specified collection.

        **Important:**
        *   Requires a connection to MongoDB Atlas.
        *   The user connecting must have sufficient privileges (e.g., AtlasAdmin).
        *   Index creation can take time depending on the data size.
        *   This method defines the index for the 'vector' field with 768 dimensions
            and cosine similarity.

        Args:
            index_name: The desired name for the vector search index.
            collection_name: The name of the collection. Uses the default if None.

        Returns:
            True if the index creation command was successfully submitted or if
            an index with that name already exists, False otherwise.
        """
        if self.db is None:
            logging.error("Cannot create index: Not connected to MongoDB. Call connect() first.")
            return False

        target_collection_name = collection_name or self.default_collection_name
        logging.info(f"Attempting to create Atlas Vector Search index '{self.INDEX_NAME}' "
                     f"on collection '{self.db_name}.{target_collection_name}' "
                     f"for field '{self.VECTOR_FIELD}' (dim: {self.EXPECTED_DIMENSION}, similarity: cosine).")

        # Atlas Vector Search index definition
        index_definition = {
            "mappings": {
                "dynamic": True,
                "fields": {
                    self.VECTOR_FIELD: {
                        "dimensions": 768,
                        "similarity": "cosine",
                        "type": "knnVector"
                    }
                }
            }
        }

        try:
            # Get the collection and use the create_search_index method (pymongo 4.6+)
            collection = self.db[target_collection_name]
            search_index_model = SearchIndexModel(definition=index_definition, name=self.INDEX_NAME)
            collection.create_search_index(search_index_model)
            logging.info(f"Successfully submitted command to create search index '{self.INDEX_NAME}' on "
                         f"'{target_collection_name}'. Index building may take time.")
            return True
        except OperationFailure as e:
            if getattr(e, "code", None) in (59, 6047401) or "no such command" in str(e).lower() or "only allowed on MongoDB Atlas" in str(e):
                logging.warning(
                    f"Atlas Search index creation is not available on this MongoDB deployment "
                    f"(plain community server, not Atlas). Documents are still inserted with "
                    f"embeddings and will be queried via in-process fallback search. Details: {e}"
                )
                return True
            logging.error(f"An operation failure occurred while trying to create search index '{self.INDEX_NAME}' "
                          f"on '{target_collection_name}'. Details: {e}", exc_info=True)
            return False
        except AttributeError as e:
            # create_search_index method not available in this pymongo version
            logging.warning(f"Vector Search index creation not supported (pymongo version may be too old). "
                           f"Documents are still inserted with embeddings and can be queried. Details: {e}")
            return True
        except Exception as e:
            logging.error(f"An unexpected error occurred during search index creation: {e}", exc_info=True)
            return False


    async def insert_data(
        self,
        data_items: List[Dict[str, Any]],
        collection_name: Optional[str] = None,
    ) -> Tuple[Optional[List[Any]], int]:
        """
        Inserts multiple data items, each including an embedding, into the specified collection.
        Creates the collection if it doesn't exist.

        Args:
            data_items: A list of dictionaries. Each dictionary must contain at least
                        an 'embedding' key (list of floats) and preferably a unique
                        'source_id' key and an optional 'metadata' dictionary.
            collection_name: The name of the collection. Uses the default if None.

        Returns:
            A tuple containing:
            - A list of the '_id's of the successfully inserted documents, or None on failure.
            - The number of documents successfully inserted.
        """
        collection = await self._get_collection(collection_name)
        if collection is None:
            # Error already logged in _get_collection
            return None, 0

        if not data_items:
            logging.info(f"No data items provided for insertion into '{collection.name}'.")
            return [], 0

        valid_documents = []
        skipped_count = 0
        for i, item in enumerate(data_items):
            if isinstance(item.get("vectorContent"), list):
                valid_documents.append(item)
            else:
                skipped_count += 1
                logging.warning(f"Skipping item at index {i} due to missing or invalid 'embedding'. Source ID: {item.get('source_id', 'N/A')}")

        if not valid_documents:
             logging.error(f"No valid documents found in the batch for insertion into '{collection.name}'. {skipped_count} items skipped.")
             return None, 0
        elif skipped_count > 0:
             logging.warning(f"Prepared {len(valid_documents)} for insertion into '{collection.name}', skipped {skipped_count} invalid items.")
        else:
            logging.info(f"Attempting to insert {len(valid_documents)} documents into '{collection.name}'.")


        try:
            result: InsertManyResult = await collection.insert_many(valid_documents, ordered=False)
            inserted_count = len(result.inserted_ids)
            logging.info(f"Successfully inserted {inserted_count} documents into collection '{collection.name}'.")
            if inserted_count < len(valid_documents):
                 # This can happen with ordered=False if duplicates or other errors occurred per-document
                 logging.warning(f"Attempted to insert {len(valid_documents)} documents, but {inserted_count} were successfully inserted into '{collection.name}'. Check for potential individual errors (e.g., unique index violations).")
            return result.inserted_ids, inserted_count
        except OperationFailure as e:
            logging.error(f"Database operation failed during batch insert into '{collection.name}'. Details: {e}", exc_info=True)
            return None, 0
        except Exception as e:
            logging.error(f"An unexpected error occurred during batch insertion into '{collection.name}': {e}", exc_info=True)
            return None, 0


    async def similarity_search(
        self,
        query_vector: List[float],
        collection_name: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Performs vector similarity search using MongoDB Atlas Vector Search.

        **Requires a configured Vector Search index in MongoDB Atlas.**

        Args:
            query_vector: The embedding vector to search with.
            collection_name: The name of the collection. Uses the default if None.
            num_candidates: Number of candidates for the k-NN search.
            limit: The maximum number of similar documents to return.

        Returns:
            A list of matching documents, including a 'score' field. Empty list on error.
        """
        collection = await self._get_collection(collection_name)
        if collection is None:
            return []

        logging.info(f"Performing similarity search in '{collection.name}' using index '{self.INDEX_NAME}' with limit {limit}.")

        vector_search_stage = {
            "$search": {
                "cosmosSearch": {
                    "vector": query_vector,
                    "path": self.VECTOR_FIELD,
                    "k": limit
                },
                "returnStoredSource": True
            }
        }

        project_stage = {
            '$project': {
                '_id': 0,
                'textContent': 1,
                'score': {'$meta': 'vectorSearchScore'}
            }
        }

        pipeline = [vector_search_stage, project_stage]

        try:
            cursor = collection.aggregate(pipeline)
            results = await cursor.to_list(length=limit)
            logging.info(f"Similarity search completed. Found {len(results)} documents.")
            return results
        except OperationFailure as e:
            if "index not found" in str(e).lower() or "unrecognized pipeline stage name: '$vectorsearch'" in str(e).lower():
                 logging.error(f"Vector search failed. Ensure the Atlas Vector Search index named '{self.INDEX_NAME}' "
                       f"exists on collection '{collection.name}', targets the 'embedding' field, "
                       f"and your cluster tier supports Vector Search. Details: {e}", exc_info=True) # Log stack trace too
            elif "$vectorSearch is not allowed" in str(e):
                 logging.error(f"$vectorSearch stage is likely not supported on your MongoDB tier or version. "
                       f"It requires MongoDB Atlas. Details: {e}", exc_info=True)
            else:
                logging.error(f"Vector search operation failed in '{collection.name}'. Details: {e}", exc_info=True)
            return []
        except Exception as e:
            logging.error(f"An unexpected error occurred during vector search in '{collection.name}': {e}", exc_info=True)
            return []

    # async def similarity_search_on_query(
    #         self,
    #         query_vector: List[float],
    #         collection_name: Optional[str] = None,
    #         limit: int = 5,
    #     ) -> List[Dict[str, Any]]:
    #         """
    #         Performs vector similarity search, filtering for active documents.
            
    #         The pipeline fetches more candidates than the limit to ensure that after
    #         filtering for 'isactive: true', we can still return up to the limit
    #         of relevant, active documents.
    #         """
    #         collection = await self._get_collection(collection_name)
    #         if collection is None:
    #             return []

    #         num_candidates = limit * 2
            
    #         logging.info(f"Performing similarity search in '{collection.name}' "
    #                     f"for {num_candidates} candidates to find {limit} active results.")

    #         # Stage 1: Perform the vector search
    #         vector_search_stage = {
    #             "$search": {
    #                 "cosmosSearch": {
    #                     "vector": query_vector,
    #                     "path": self.VECTOR_FIELD,
    #                     "k": num_candidates # Use the higher candidate count
    #                 },
    #                 "returnStoredSource": True
    #             }
    #         }

    #         # Stage 2: Filter results for active documents
    #         match_stage = {
    #             "$match": {
    #                 "metadata.isactive": True
    #             }
    #         }

    #         # Stage 3: Project the fields into the desired format
    #         project_stage = {
    #             '$project': {
    #                 'id': '$metadata.uuid',
    #                 'score': {'$meta': 'vectorSearchScore'},
    #                 'metadata': 1,
    #                 'textContent': 1,
    #                 '_id': 0
    #             }
    #         }
            
    #         # Stage 4: Limit the final output
    #         limit_stage = {
    #             "$limit": limit
    #         }

    #         pipeline = [
    #             vector_search_stage,
    #             match_stage,
    #             project_stage,
    #             limit_stage
    #         ]

    #         try:
    #             cursor = collection.aggregate(pipeline)
    #             results = await cursor.to_list(length=limit)
    #             logging.info(f"Similarity search completed. Found {len(results)} active documents.")
    #             return results
    #         except OperationFailure as e:
    #             logging.error(f"Vector search operation failed in '{collection.name}'. Details: {e}", exc_info=True)
    #             return []
    #         except Exception as e:
    #             logging.error(f"An unexpected error occurred during vector search in '{collection.name}': {e}", exc_info=True)
    #             return []

    async def similarity_search_on_query(
            self,
            query_vector: List[float],
            collection_name: str,
            limit: int = 10,
        ) -> List[Dict[str, Any]]:
            """
            Performs vector similarity search, filtering for active documents.
            
            The pipeline fetches more candidates than the limit to ensure that after
            filtering for 'isactive: true', we can still return up to the limit
            of relevant, active documents.
            """
            num_candidates = limit * 2
            
            logging.info(f"Performing similarity search in '{collection_name}' "
                        f"for {num_candidates} candidates to find {limit} active results.")

            pipeline = [
                {
                "$vectorSearch": {
                    "index": self.INDEX_NAME,
                    "path": self.VECTOR_FIELD,
                    "queryVector": query_vector,
                    "numCandidates": limit * 10,
                    "limit": limit,
                    "filter": {"metadata.isactive": True}
                }
                },
                {
                "$project": {
                    "id": "$metadata.uuid",
                    "textContent": 1,
                    "metadata": 1,
                    "score": {"$meta": "vectorSearchScore"},
                    "_id": 0
                }
                }
            ]

            try:
                cursor = self.db[collection_name].aggregate(pipeline)
                results = await cursor.to_list(length=limit)
                logging.info(f"Similarity search completed. Found {len(results)} active documents.")
                return results
            except OperationFailure as e:
                if getattr(e, "code", None) in (31082, 6047401) or "SearchNotEnabled" in str(e) or "only allowed on MongoDB Atlas" in str(e):
                    logging.warning(
                        f"Atlas $vectorSearch is not available on this MongoDB deployment "
                        f"(plain community server, not Atlas/Atlas-CLI-local). Falling back "
                        f"to in-process cosine similarity for '{collection_name}'."
                    )
                    return await self._similarity_search_fallback(query_vector, collection_name, limit)
                logging.error(f"Vector search operation failed in '{collection_name}'. Details: {e}", exc_info=True)
                return []
            except Exception as e:
                logging.error(f"An unexpected error occurred during vector search in '{collection_name}': {e}", exc_info=True)
                return []

    async def _similarity_search_fallback(
            self,
            query_vector: List[float],
            collection_name: str,
            limit: int,
        ) -> List[Dict[str, Any]]:
            """
            Brute-force cosine-similarity search, used when Atlas $vectorSearch isn't
            available (plain community MongoDB). Fine for local/dev-sized collections;
            not a substitute for a real vector index at production scale.
            """
            import numpy as np

            cursor = self.db[collection_name].find(
                {"metadata.isactive": True, self.VECTOR_FIELD: {"$exists": True}},
                {"textContent": 1, "metadata": 1, self.VECTOR_FIELD: 1, "_id": 0},
            )
            docs = await cursor.to_list(length=None)
            if not docs:
                return []

            query_vec = np.array(query_vector, dtype=float)
            query_norm = np.linalg.norm(query_vec)

            scored = []
            for doc in docs:
                vec = doc.get(self.VECTOR_FIELD)
                if not vec:
                    continue
                doc_vec = np.array(vec, dtype=float)
                denom = query_norm * np.linalg.norm(doc_vec)
                score = float(np.dot(query_vec, doc_vec) / denom) if denom else 0.0
                scored.append((score, doc))

            scored.sort(key=lambda pair: pair[0], reverse=True)

            results = []
            for score, doc in scored[:limit]:
                metadata = doc.get("metadata", {})
                results.append({
                    "id": metadata.get("uuid"),
                    "textContent": doc.get("textContent"),
                    "metadata": metadata,
                    "score": score,
                })
            logging.info(f"Fallback similarity search completed. Found {len(results)} active documents.")
            return results


    async def deactivate_data_by_source_id(
        self,
        collection_name: str,
        source_file_id: str,
    ) -> int:
        """
        Performs a "soft delete" by setting 'metadata.isactive' to false for all
        documents that match a given source_file_id.

        Args:
            collection_name: The name of the collection.
            source_file_id: The ID of the source file, used to find all its chunks.

        Returns:
            The number of documents that were successfully updated (deactivated).
        """
        collection = await self._get_collection(collection_name)
        if collection is None:
            return 0

        logging.info(f"Attempting to deactivate documents in '{collection.name}' "
                     f"with source_file_id: {source_file_id}")
        try:
            filter_query = {"metadata.source_file_id": source_file_id}
            
            update_operation = {
                "$set": {
                    "metadata.isactive": False
                }
            }

            result: UpdateResult = await collection.update_many(
                filter_query, 
                update_operation
            )
            
            modified_count = result.modified_count
            logging.info(f"Successfully deactivated {modified_count} documents "
                         f"in '{collection.name}'.")
            
            return modified_count
            
        except OperationFailure as e:
            logging.error(f"Database operation failed during deactivation in '{collection.name}': {e}", exc_info=True)
            raise
        except Exception as e:
            logging.error(f"An unexpected error occurred during deactivation in '{collection.name}': {e}", exc_info=True)
            raise

    # --- Context Manager Support ---
    async def __aenter__(self):
        """Async context manager entry point: connects to the database."""
        connected = await self.connect()
        if not connected:
            raise ConnectionError("Failed to connect to MongoDB.")
        return self


    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit point: closes the connection."""
        self.close()
        if exc_type:
            # Log the exception passed to __aexit__
            logging.error(f"An exception occurred within the async context: {exc_type.__name__}: {exc_val}", exc_info=(exc_type, exc_val, exc_tb))
        # Return False to propagate the exception (default behavior)
        return False
