"""Document parsing entry point: pick extractor by extension, store text.

Flow:
1. Route to the right extractor by extension.
2. Write extracted text back to the store; return the new blob path.
"""
import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from app.logic.storage_paths import file_extension, text_blob_path
from app.logic.text_formats import (
    blank_page_share,
    decode_txt,
    eml_to_text,
    has_text,
)
from app.parsing.extractors import (
    docx_helper,
    html_helper,
    image_helper,
    msg_to_text,
    pptx_helper,
    pymupdf_helper,
    xlsx_helper,
)
from app.parsing.vision.service import VisionService
from app.services.storage.storage_service import FileStorageService

logger = logging.getLogger(__name__)

# (file bytes, vision client, lowercased extension) -> text
Extractor = Callable[[bytes, VisionService, str], Awaitable[str]]


def _in_thread(transform: Callable[[bytes], str]) -> Extractor:
    async def extract(raw: bytes, vision: VisionService, suffix: str) -> str:
        return await asyncio.to_thread(transform, raw)
    return extract


EXTRACTORS: dict[str, Extractor] = {
    ".pdf":  pymupdf_helper,
    ".docx": docx_helper,
    ".doc":  docx_helper,
    ".pptx": pptx_helper,
    ".ppt":  pptx_helper,
    ".xlsx": xlsx_helper,
    ".html": html_helper,
    ".htm":  html_helper,
    ".png":  image_helper,
    ".jpeg": image_helper,
    ".jpg":  image_helper,
    ".webp": image_helper,
    ".txt":  _in_thread(decode_txt),
    ".eml":  _in_thread(eml_to_text),
    ".msg":  _in_thread(msg_to_text),
}

# Formats whose extractors produce ATX-markdown — stored as .md.
MARKDOWN_SUFFIXES = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".html", ".htm"}
VISION_SUFFIXES = {".png", ".jpeg", ".jpg", ".webp"}


async def parsing_handler(
    file_path: str,
    storage_service: FileStorageService,
    vision: VisionService,
    max_blank_page_share: float = 1.0,
) -> str:
    """Extract text from `file_path`, store it beside the source, return the new path."""
    if not await storage_service.exists(file_path):
        raise FileNotFoundError(f"'{file_path}' does not exist in the file store.")

    suffix = file_extension(file_path)
    extract = EXTRACTORS.get(suffix)
    if extract is None:
        raise ValueError(f"File with extension '{suffix}' is not supported right now.")

    t0 = time.perf_counter()
    raw = (await storage_service.download_file(file_path)).read()
    download_ms = round((time.perf_counter() - t0) * 1000)

    t1 = time.perf_counter()
    text = await extract(raw, vision, suffix)
    extract_ms = round((time.perf_counter() - t1) * 1000)

    if not has_text(text):
        raise ValueError(
            f"No text could be extracted from '{file_path}' (extension '{suffix}'). "
            "The file may be empty, an image/scan without readable text, or the "
            "extraction step returned no content."
        )

    blank, pages = blank_page_share(text)
    if pages >= 2 and blank / pages > max_blank_page_share:
        raise ValueError(
            f"Only {pages - blank} of {pages} pages of '{file_path}' had readable text. "
            "Check the file, or set VISION_LLM_TYPE so scanned pages are transcribed."
        )

    extension = ".md" if suffix in MARKDOWN_SUFFIXES or suffix in VISION_SUFFIXES else ".txt"
    blob_path = text_blob_path(file_path, str(uuid.uuid4()), extension)

    t2 = time.perf_counter()
    await storage_service.upload_text(blob_path, text)
    upload_ms = round((time.perf_counter() - t2) * 1000)

    logger.info(
        "Parse timing: ext=%s download_ms=%d extract_ms=%d upload_ms=%d",
        suffix, download_ms, extract_ms, upload_ms,
    )
    return blob_path
