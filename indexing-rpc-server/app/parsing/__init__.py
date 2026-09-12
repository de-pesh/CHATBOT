"""Document parsing: text extraction and OCR.

Exposed over gRPC as ParsingService.ParseFile. It lived in the chat service as
an in-process call for a while; it sits here now so the OCR toolchain
(pymupdf, ocrmypdf, ghostscript, the vision model) stays in the compute image
alongside torch, and the control plane keeps none of it.

    handler.py      entry point, file-type dispatch, the extension enum
    extractors.py   per-format text extraction
    ollama.py       vision model, used only for images and scanned pages

Blob access reuses app.services.azure_blob_storage_service rather than the
near-identical copy this package used to carry.
"""
from app.parsing.handler import parsing_handler
from app.parsing.ollama import OllamaService

__all__ = ["parsing_handler", "OllamaService"]
