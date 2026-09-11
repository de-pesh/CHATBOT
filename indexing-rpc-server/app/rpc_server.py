import os
import grpc
from concurrent import futures
from google.protobuf.json_format import MessageToDict, ParseDict
from app.services.azure_blob_storage_service import AzureBlobService
from app.services.embedding_service import EmbeddingService
from app.services.mongo_service import MongoEmbeddingStore
from app.config import get_settings
from app.controllers.index_controller import index_handler
from app import indexing_service_pb2
from app import indexing_service_pb2_grpc

settings = get_settings()

azure_blob_service = AzureBlobService(
    blob_connection_string=settings.BLOB_CONNECTION_STRING.get_secret_value(),
    container_name=settings.BLOB_CONTAINER_NAME,
)

embedding_service = EmbeddingService(model_path=settings.EMBEDDING_MODEL_PATH)


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
            async with MongoEmbeddingStore(
                mongo_uri=settings.MONGO_CONNECTION_STRING.get_secret_value(),
                db_name=settings.MONGO_DATABASE_NAME,
                default_collection_name="CPX_chatbot_test"
            ) as mongo_service:
                source_file_id = await index_handler(
                    collection_name=request.collection_name,
                    file_path=request.file_path,
                    metadata=MessageToDict(request.metadata),
                    azure_blob_service=azure_blob_service,
                    mongo_service=mongo_service,
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
            async with MongoEmbeddingStore(
                mongo_uri=settings.MONGO_CONNECTION_STRING.get_secret_value(),
                db_name=settings.MONGO_DATABASE_NAME,
                default_collection_name="CPX_chatbot_test"
            ) as mongo_service:
                # Step 1: Deactivate old chunks associated with the source_file_id
                deactivated_count = await mongo_service.deactivate_data_by_source_id(
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
                    azure_blob_service=azure_blob_service,
                    mongo_service=mongo_service,
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
            async with MongoEmbeddingStore(
                mongo_uri=settings.MONGO_CONNECTION_STRING.get_secret_value(),
                db_name=settings.MONGO_DATABASE_NAME,
                default_collection_name="CPX_chatbot_test"
            ) as mongo_service:
                deactivated_count = await mongo_service.deactivate_data_by_source_id(
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

            async with MongoEmbeddingStore(
                mongo_uri=settings.MONGO_CONNECTION_STRING.get_secret_value(),
                db_name=settings.MONGO_DATABASE_NAME,
                default_collection_name="CPX_chatbot_test" 
            ) as mongo_service:
                search_hits = await mongo_service.similarity_search_on_query(
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


async def serve_vector_service():
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=int(os.cpu_count())))
    indexing_service_pb2_grpc.add_VectorServiceServicer_to_server(VectorServiceServer(), server)
    server.add_insecure_port(f"[::]:{settings.GRPC_PORT}")
    print(f"VectorService gRPC server is running on port {settings.GRPC_PORT}...")
    await server.start()
    await server.wait_for_termination()


async def serve():
    await serve_vector_service()
