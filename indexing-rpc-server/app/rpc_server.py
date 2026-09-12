"""gRPC surface of the compute service.

Nothing here names a concrete backend. The file store and the vector database
both come from factories driven by config, so swapping either is a setting plus
an implementation of the corresponding interface - see services/storage_service.py
and services/vector_store.py.
"""
import os
import grpc
from concurrent import futures
from google.protobuf.json_format import MessageToDict, ParseDict
from app.services.storage_factory import create_storage_service
from app.services.vector_store_factory import create_vector_store
from app.services.embedding_service import EmbeddingService
from app.config import get_settings
from app.controllers.index_controller import index_handler
from app.parsing import parsing_handler, OllamaService
from app import indexing_service_pb2
from app import indexing_service_pb2_grpc

settings = get_settings()

storage_service = create_storage_service(settings)

embedding_service = EmbeddingService(model_path=settings.EMBEDDING_MODEL_PATH)

# Only reached for images and scanned pages; built once either way.
ollama_client = OllamaService(
    model=settings.OLLAMA_MODEL,
    base_url=settings.OLLAMA_BASE_URL,
    api_key=settings.OLLAMA_API_KEY.get_secret_value(),
    temperature=settings.OLLAMA_TEMPERATURE,
)


def _vector_store():
    """A vector store for one RPC, opened and closed around the call."""
    return create_vector_store(settings)


class VectorServiceServer(indexing_service_pb2_grpc.VectorServiceServicer):
    """
    Implements the VectorService gRPC service.
    """
    async def Index(self, request: indexing_service_pb2.IndexRequest, context):
        """
        Handles the Index RPC call. It indexes a document and returns a single
        source file ID for the entire operation. It now considers the `is_policy` flag.
        """
        try:
            async with _vector_store() as vector_store:
                source_file_id = await index_handler(
                    collection_name=request.collection_name,
                    file_path=request.file_path,
                    metadata=MessageToDict(request.metadata),
                    storage_service=storage_service,
                    vector_store=vector_store,
                    embedding_service=embedding_service,
                    is_policy=request.is_policy  # Pass the new flag
                )

            return indexing_service_pb2.IndexResponse(
                success=True,
                message="Indexing completed successfully.",
                file_id=source_file_id
            )
        except Exception as e:
            context.set_details(f"Error during indexing: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return indexing_service_pb2.IndexResponse(
                success=False,
                message=str(e),
                file_id=""
            )

    async def Update(self, request: indexing_service_pb2.UpdateRequest, context):
        """
        Handles the Update RPC call. This operation first deactivates all
        existing document chunks associated with the given file_id and then
        indexes the new file content using the same file_id.
        """
        try:
            async with _vector_store() as vector_store:
                # Step 1: Deactivate old chunks associated with the source_file_id
                deactivated_count = await vector_store.deactivate_data_by_source_id(
                    collection_name=request.collection_name,
                    source_file_id=request.file_id
                )
                print(f"Deactivated {deactivated_count} chunks for file_id: {request.file_id}")

                # Step 2: Index the new document using the REFACTORED index_handler,
                # passing the existing file_id to ensure continuity.
                updated_file_id = await index_handler(
                    collection_name=request.collection_name,
                    file_path=request.file_path,
                    metadata=MessageToDict(request.metadata),
                    storage_service=storage_service,
                    vector_store=vector_store,
                    embedding_service=embedding_service,
                    is_policy=request.is_policy,
                    source_file_id=request.file_id  # Pass the existing ID
                )

            return indexing_service_pb2.UpdateResponse(
                success=True,
                message="Document updated successfully.",
                file_id=updated_file_id
            )
        except Exception as e:
            context.set_details(f"Error during update: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return indexing_service_pb2.UpdateResponse(
                success=False,
                message=str(e),
                file_id=request.file_id # Return the ID even on failure
            )

    async def Delete(self, request: indexing_service_pb2.DeleteRequest, context):
        """
        Handles the Delete RPC call. This is a "soft delete" that sets
        'isactive' to false for all document chunks associated with a given
        source file ID.
        """
        try:
            async with _vector_store() as vector_store:
                deactivated_count = await vector_store.deactivate_data_by_source_id(
                    collection_name=request.collection_name,
                    source_file_id=request.file_id
                )

            return indexing_service_pb2.DeleteResponse(
                success=True,
                message=f"Successfully deactivated {deactivated_count} document chunks."
            )
        except Exception as e:
            context.set_details(f"Error during deactivation: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return indexing_service_pb2.DeleteResponse(
                success=False,
                message=str(e)
            )

    async def Search(self, request: indexing_service_pb2.SearchRequest, context):
        """
        Handles the Search RPC call. Performs a similarity search on a collection
        and returns a list of matching documents.
        """
        try:
            query_vector_np = embedding_service.generate_embeddings(request.query)

            query_vector = query_vector_np.tolist()

            async with _vector_store() as vector_store:
                search_hits = await vector_store.similarity_search_on_query(
                    query_vector=query_vector,
                    collection_name=request.collection_name,
                    limit=10
                )

            response = indexing_service_pb2.SearchResponse()
            for hit in search_hits:
                search_result = response.results.add()

                search_result.id = hit.get('id', '')
                search_result.score = hit.get('score', 0.0)

                metadata_dict = hit.get('metadata', {})

                text_content = hit.get('textContent', '')
                metadata_dict['textContent'] = text_content

                if metadata_dict:
                    ParseDict(metadata_dict, search_result.metadata)

            return response
        except Exception as e:
            context.set_details(f"Error during search: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return indexing_service_pb2.SearchResponse()

    async def ListDocuments(self, request: indexing_service_pb2.ListDocumentsRequest, context):
        """Groups the live chunks back into one entry per source document.

        main-service used to run this aggregation against Mongo itself, which
        gave the control plane a vector-database client and a schema
        dependency. Keeping it here is what makes the store swappable.
        """
        try:
            async with _vector_store() as vector_store:
                documents = await vector_store.list_documents(
                    collection_name=request.collection_name
                )

            response = indexing_service_pb2.ListDocumentsResponse(success=True)
            for document in documents:
                summary = response.documents.add()
                summary.file_id = document.get("file_id", "")
                summary.file_name = document.get("file_name", "")
                summary.chunk_count = int(document.get("chunk_count", 0))
            return response
        except Exception as e:
            context.set_details(f"Error during listing: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return indexing_service_pb2.ListDocumentsResponse(success=False, message=str(e))


class ParsingServiceServer(indexing_service_pb2_grpc.ParsingServiceServicer):
    """Implements the ParsingService gRPC service."""

    async def ParseFile(self, request: indexing_service_pb2.ParseFileRequest, context):
        """Extracts text from the stored file at request.file_path.

        The caller uploads the original and passes its store path; the extracted
        text is written back to the store and that path returned, ready to hand
        straight to Index. Failures come back as success=False with the reason
        rather than an empty path, so the caller does not have to guess.
        """
        try:
            parsed_blob_path = await parsing_handler(
                file_path=request.file_path,
                storage_service=storage_service,
                ollama_client=ollama_client,
            )
            if not parsed_blob_path:
                return indexing_service_pb2.ParseFileResponse(
                    success=False, message="Parsing produced no output.")
            return indexing_service_pb2.ParseFileResponse(
                success=True, blob_path=parsed_blob_path)
        except Exception as e:
            # A bad document must not take the server down, and the caller
            # needs the reason: an unsupported extension and a corrupt PDF are
            # different problems.
            print(f"ParseFile failed for {request.file_path}: {e}")
            return indexing_service_pb2.ParseFileResponse(
                success=False, message=f"{type(e).__name__}: {e}")


async def serve():
    """Serves VectorService and ParsingService on the same port."""
    await storage_service.initialize()
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=int(os.cpu_count())))
    indexing_service_pb2_grpc.add_VectorServiceServicer_to_server(VectorServiceServer(), server)
    indexing_service_pb2_grpc.add_ParsingServiceServicer_to_server(ParsingServiceServer(), server)
    server.add_insecure_port(f"[::]:{settings.GRPC_PORT}")
    print(
        f"VectorService and ParsingService gRPC server is running on port {settings.GRPC_PORT} "
        f"(STORAGE_TYPE={settings.STORAGE_TYPE}, VECTOR_STORE={settings.VECTOR_STORE})..."
    )
    await server.start()
    await server.wait_for_termination()
