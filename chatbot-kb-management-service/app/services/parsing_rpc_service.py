import sys
import os
import logging
# Add the project root directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import grpc
from app import parsing_service_pb2
from app import parsing_service_pb2_grpc
from app.config import Settings

logger = logging.getLogger(__name__)

class ParsingRPCService:
    """
        Parses a file using the remote parsing gRPC service.

        Args:
            settings (Settings): Application settings containing the RPC server info.
            file_path (str): The path to the file to be parsed.

        Returns:
            str: The blob path containing the parsed file.

        Raises:
            grpc.RpcError: If a gRPC error occurs during the call.
        """
    @staticmethod
    async def parse_file(
        settings: Settings,
        file_path: str,
    ):
        with grpc.insecure_channel(f"{settings.PARSING_RPC_SERVER}") as channel:
            stub = parsing_service_pb2_grpc.ParsingServiceStub(channel)
            response = stub.ParseFile(parsing_service_pb2.ParseFileRequest(file_path=file_path))
            logger.debug(f"Blob Path containg the parsed file: {response.blob_path}")
            return response.blob_path