import asyncio
import logging
import os
import sys
import grpc
from concurrent import futures
from app.controllers.parsing_controller import parsing_handler
from app.services.azure_blob_storage_service import AzureBlobService
from app.services.ollama_service import OllamaService
from app.config import get_settings
from app import parsing_service_pb2
from app import parsing_service_pb2_grpc

# Load settings
try:
    settings = get_settings()
except Exception as e:
    print(f"FATAL: Unexpected error obtaining settings: {e}")
    sys.exit(1)

# --- Basic Setup ---
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ParsingWorker")

# Initialize Azure Blob Service
azure_blob_service = AzureBlobService(
    blob_connection_string=settings.BLOB_CONNECTION_STRING.get_secret_value(),
    container_name=settings.BLOB_CONTAINER_NAME,
)

ollama_client = OllamaService(
        model=settings.OLLAMA_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        api_key=settings.OLLAMA_API_KEY.get_secret_value(),
        temperature=settings.OLLAMA_TEMPERATURE
    )

# Implement the gRPC service
class ParsingServiceServicer(parsing_service_pb2_grpc.ParsingServiceServicer):
    async def ParseFile(self, request, context):
        """
        Handles the ParseFile RPC call.
        """
        file_path = request.file_path
        try:
            blob_path = await parsing_handler(
                file_path=file_path,
                azure_blob_service=azure_blob_service,
                ollama_client=ollama_client
            )
            print(f"File parsed and uploaded to blob: {blob_path}")
            return parsing_service_pb2.ParseFileResponse(blob_path=blob_path)
        except Exception as e:
            context.set_details(f"Error parsing file: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            return parsing_service_pb2.ParseFileResponse(blob_path="")

# Start the gRPC server
async def serve():
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=int(os.cpu_count())))
    parsing_service_pb2_grpc.add_ParsingServiceServicer_to_server(ParsingServiceServicer(), server)
    server.add_insecure_port(f"[::]:{settings.GRPC_PORT}")
    logger.info(f"gRPC server is running on port {settings.GRPC_PORT}...")
    await server.start()
    await server.wait_for_termination()
