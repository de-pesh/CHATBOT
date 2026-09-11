'''
Module: pdf_helper

This module provides a helper function to extract text from PDF files.
'''

import logging
from io import BytesIO
import fitz
from app.utils.helpers.base_extractor import pdf_text_extraction
from app.services.ollama_service import OllamaService


async def pdf_helper(file_stream: BytesIO, ollama_client: OllamaService) -> str:
    """
    Processes the PDF based on whether it contains extractable text or not.
    If scanned (no text found), sends images to LLM for OCR.
    Returns combined text string.
    """
    doc = fitz.open(stream=file_stream, filetype="pdf")
    logging.info("Text extraction process started for PDF")
    # Send the PDF document for text extraction
    return await pdf_text_extraction(doc, ollama_client)
