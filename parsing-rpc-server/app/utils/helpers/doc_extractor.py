'''
Module: docx_helper

This module provides a helper function to extract text from DOCX files.
'''

import tempfile
import subprocess
import os
import logging
import shutil
from io import BytesIO
import fitz
from app.utils.helpers.base_extractor import pdf_text_extraction
from app.services.ollama_service import OllamaService


def _resolve_soffice_path() -> str:
    """
    Resolves the LibreOffice executable path from PATH.
    """
    soffice_path = shutil.which("soffice")
    if not soffice_path:
        raise RuntimeError(
            "LibreOffice executable 'soffice' was not found in PATH. "
            "Install LibreOffice in the parser runtime image."
        )
    return soffice_path

async def docx_helper(
        file_stream: BytesIO,
        ollama_client: OllamaService,
        suffix: str,
):
    '''
    Extracts text from a DOCX file stream.

    Parameters:
        file_stream (BytesIO): The file stream of the DOCX document.

    Returns:
        str: Extracted text from the DOCX file.
    '''
    logging.info("Text extraction process started for DOCX")
    # Create a temporary file to store the DOCX content
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_docx:
        temp_docx_path = temp_docx.name
        temp_docx.write(file_stream.read())
        temp_docx.flush()
    temp_dir = os.path.dirname(temp_docx_path)
    converted_path = ""
    try:
        libre_office = _resolve_soffice_path()
        # Convert the DOCX file to a PDF using LibreOffice
        result = subprocess.run(
            [libre_office, '--headless', '--convert-to', 'pdf', '--outdir', temp_dir, temp_docx_path],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            logging.error(f"LibreOffice conversion failed: {result.stderr}")
            raise Exception("LibreOffice conversion failed")

        # LibreOffice saves the PDF with the same filename but .pdf extension
        # We need to handle the extension replacement carefully
        base_name = os.path.splitext(os.path.basename(temp_docx_path))[0]
        converted_path = os.path.join(temp_dir, base_name + ".pdf")
        if not os.path.exists(converted_path):
            raise RuntimeError(f"Expected converted PDF was not created at: {converted_path}")

    except Exception as e:
        logging.error(str(e))
        raise e
    try:
        with fitz.open(converted_path) as doc:
            # Extract text from the converted PDF
            return await pdf_text_extraction(doc, ollama_client)
    finally:
        if os.path.exists(temp_docx_path):
            os.remove(temp_docx_path)
        if converted_path and os.path.exists(converted_path):
            os.remove(converted_path)
        