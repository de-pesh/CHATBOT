import grpc
import os
import sys
import logging
from app.config import Settings
# Protobuf imports
from google.protobuf.json_format import ParseDict
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import indexing_service_pb2
from app import indexing_service_pb2_grpc

logger = logging.getLogger(__name__)


class IndexingRPCService:
    @staticmethod
    async def indexing_add_file(
        settings: Settings,
        parsed_file_path: str,
        metadata: dict,
    ):
        """
        Indexes a document (file) using the remote indexing gRPC service.

        Args:
            settings (Settings): Application settings containing RPC server and collection info.
            parsed_file_path (str): The path to the parsed file to be indexed.
            metadata (dict): Metadata to associate with the file.

        Returns:
            str: The file_id assigned by the indexing service.

        Raises:
            Exception: If indexing fails or no file_id is returned.
            grpc.aio.AioRpcError: If a gRPC error occurs during the call.
        """
        async with grpc.aio.insecure_channel(f"{settings.RPC_SERVER}") as channel:
            stub = indexing_service_pb2_grpc.VectorServiceStub(channel)

            # --- INDEX a document to get a file_id ---
            metadata_struct = indexing_service_pb2.google_dot_protobuf_dot_struct__pb2.Struct()
            ParseDict(metadata, metadata_struct)
            # --- UPDATED: Use the new IndexRequest and call the 'Index' RPC ---
            index_request = indexing_service_pb2.IndexRequest(
                collection_name=settings.MONGODB_COLLECTION_NAME,
                file_path=parsed_file_path,
                metadata=metadata_struct,
                is_policy=True,
            )

            try:
                # --- UPDATED: Call the 'Index' RPC, not 'IndexHandler' ---
                index_response = await stub.Index(index_request)
                # --- UPDATED: Check for 'file_id', not 'generated_uuids' ---
                if index_response.success and index_response.file_id:
                    file_id = index_response.file_id
                    logger.info(f"Success: Document indexed with file_id: {file_id}\n")
                else:
                    logger.error(f"Indexing failed: {index_response.message}")
                    raise
            except grpc.aio.AioRpcError as e:
                logger.error(f"RPC error during Index: {e.code()} - {e.details()}")
                raise

            if not file_id:
                logger.error("Could not get a file_id to test Search/Delete. Exiting.")
                raise
            else:
                return file_id
            
    @staticmethod
    async def indexing_update_file(
        settings: Settings,
        source_file_id: str,
        parsed_file_path: str,
        metadata: dict,
    ):
        """
        Updates an existing indexed file using the remote indexing gRPC service.

        Args:
            settings (Settings): Application settings containing RPC server and collection info.
            source_file_id (str): The file_id of the file to update.
            parsed_file_path (str): The path to the new parsed file.
            metadata (dict): Metadata to update for the file.

        Returns:
            None

        Raises:
            Exception: If update fails.
            grpc.aio.AioRpcError: If a gRPC error occurs during the call.
        """
        async with grpc.aio.insecure_channel(f"{settings.RPC_SERVER}") as channel:
            stub = indexing_service_pb2_grpc.VectorServiceStub(channel)
            metadata_struct = indexing_service_pb2.google_dot_protobuf_dot_struct__pb2.Struct()
            ParseDict(metadata, metadata_struct)
            update_request = indexing_service_pb2.UpdateRequest(
                collection_name=settings.MONGODB_COLLECTION_NAME,
                file_id=source_file_id,
                file_path=parsed_file_path,
                metadata=metadata_struct,
                is_policy=True
            )
            try:
                update_response = await stub.Update(update_request)
                if update_response.success:
                    logger.info(f"Success: {update_response.message}")
                    logger.info(f"- Succesfully updated policy with the file id: {update_response.file_id}")
                    return
                else:
                    logger.error(f"Update failed: {update_response.message}")
                    raise
            except grpc.aio.AioRpcError as e:
                logger.error(f"RPC error during Index: {e.code()} - {e.details()}")
                raise
            
            
    @staticmethod
    async def indexing_delete_file(
        settings: Settings,
        source_file_id: str, 
    ):
        """
        Deletes an indexed file from the remote indexing gRPC service.

        Args:
            settings (Settings): Application settings containing RPC server and collection info.
            source_file_id (str): The file_id of the file to delete.

        Returns:
            None

        Raises:
            Exception: If deletion fails.
            grpc.aio.AioRpcError: If a gRPC error occurs during the call.
        """
        async with grpc.aio.insecure_channel(f"{settings.RPC_SERVER}") as channel:
            stub = indexing_service_pb2_grpc.VectorServiceStub(channel)
            delete_request = indexing_service_pb2.DeleteRequest(
                collection_name=settings.MONGODB_COLLECTION_NAME,
                file_id=source_file_id
            )
            try:
                # --- UPDATED: Call the 'Delete' RPC, not 'DeleteData' ---
                delete_response = await stub.Delete(delete_request)
                if delete_response.success:
                    logger.info(f"Success: {delete_response.message}")
                    return
                else:
                    logger.error(f"Deletion failed: {delete_response.message}")
                    raise
            except grpc.aio.AioRpcError as e:
                logger.error(f"RPC error during Delete: {e.code()} - {e.details()}")
                raise
