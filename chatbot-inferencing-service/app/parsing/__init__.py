"""Document parsing, moved in-process from the former parsing-rpc-server.

That service existed to expose a single gRPC method, ParseFile. Its modules took
their dependencies by injection, so folding them in removed a container, a proto
and a network hop without changing the parsing logic.

Four modules, flattened from the seventeen-file tree the move first produced:
    handler.py      entry point, file-type dispatch, the extension enum
    extractors.py   per-format text extraction
    blob.py         Azure Blob read/write
    ollama.py       vision model, used only for images and scanned pages
"""
from app.parsing.blob import AzureBlobService
from app.parsing.handler import parsing_handler
from app.parsing.ollama import OllamaService

__all__ = ["parsing_handler", "AzureBlobService", "OllamaService"]
