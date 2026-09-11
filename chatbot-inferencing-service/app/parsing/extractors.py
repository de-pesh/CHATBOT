"""Per-format text extraction.

One module per file type was six files plus two empty package markers, with a
single shared helper (pdf_text_extraction) between them. They are all small,
all called from the same place, and they all take the same two arguments.
"""
import base64
import logging
import os
import shutil
import subprocess
import tempfile
from io import BytesIO

import fitz
import ocrmypdf
from openpyxl import load_workbook
from PIL import Image

from app.parsing.ollama import OllamaService

async def advanced_ocr_pipeline(
        doc:fitz.Document,
        ollama_client: OllamaService,
):
    """
    Converts each page of the scanned PDF into an image and sends to llm_func.
    """
    input_path = None
    ocr_pdf_path = None
    ocr_doc = None
    try:
        logging.info("Scanned PDF detected. Starting advanced OCR approach.")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as input_pdf, \
            tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as ocr_pdf:
            input_path = input_pdf.name
            ocr_pdf_path = ocr_pdf.name

        doc.save(input_path)
        ocrmypdf.ocr(
            input_path,
            ocr_pdf_path,
            language="eng+ara",
            deskew=True,
            force_ocr=True
        )

        # Step 3: Open OCR'd document
        ocr_doc = fitz.open(ocr_pdf_path)
        extracted_texts = []
        # Iterate through each page of the PDF
        for page_num, page in enumerate(ocr_doc, start=1):
            try:
                ocr_text = page.get_text()
                pix = page.get_pixmap(dpi=150)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                img = img.resize((pix.width // 2, pix.height // 2))
                buffered = BytesIO()
                img.save(buffered, format="PNG")
                image_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                # Send the base64-encoded image to the LLM for text extraction
                img_text = (await ollama_client.extract_text_from_image(image_base64))
                page_text = await ollama_client.consolidate_text(ocr_text, img_text)
                extracted_texts.append(f"{page_text}\n\n{'-'*50}\n\n")

            except Exception as e:
                logging.error(f"Error processing page {page_num}: {e}")
                extracted_texts.append("")
        return "\n".join(extracted_texts)
    
    except Exception as e:
        logging.error(f"Error rendering pages as images: {e}")
        raise

    finally:
        logging.info("Cleaning up temporary files.")
        # Close the OCR'd document before removing the underlying file.
        if ocr_doc is not None:
            ocr_doc.close()
        # Clean up temporary files if they were created.
        if input_path and os.path.exists(input_path):
            os.remove(input_path)
        if ocr_pdf_path and os.path.exists(ocr_pdf_path):
            os.remove(ocr_pdf_path)


async def pdf_text_extraction(doc:fitz.Document, ollama_client: OllamaService):
    extracted_text = ""
    has_text = False
    for page in doc:
        page_text = page.get_text()
        if page_text.strip():
            has_text = True
        extracted_text += f"\n{page_text}\n\n{'-'*50}\n\n"

    if has_text:
        # If the PDF contains text, return the extracted text
        return extracted_text
    else:
        # If no text is found, process the scanned PDF for text extraction
        return await advanced_ocr_pipeline(doc, ollama_client)


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
        


async def image_helper(
        file_stream: BytesIO,
        ollama_client: OllamaService
):
    """
    Extract text from an image using OCR and LLM image understanding.

    Args:
        file_stream (bytes): The image file stream.

    Returns:
        dict: A dictionary containing the extracted text.
    """
    try:
        file_stream.seek(0)
        # Convert the image to base64
        image_base64 = base64.b64encode(file_stream.read()).decode('utf-8')
        # Send the base64-encoded image to the LLM for text extraction
        extracted_text = await ollama_client.extract_text_from_image(image_base64)
        return extracted_text
      
    except Exception as e:
        logging.error(str(e))
        raise e


async def txt_helper(file_stream: BytesIO) -> str:
    '''
    Extracts text from a plain text (.txt) file stream.

    The bytes are decoded by trying a small set of common encodings. As a final
    fallback, the content is decoded with replacement so a single bad byte never
    fails the whole parsing job.

    Parameters:
        file_stream (BytesIO): The file stream of the text document.

    Returns:
        str: The decoded text content.
    '''
    logging.info("Text extraction process started for TXT")
    file_stream.seek(0)
    raw_bytes = file_stream.read()

    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue

    # Final fallback: never fail on a stray byte.
    return raw_bytes.decode("utf-8", errors="replace")


async def xlsx_helper(file_stream: BytesIO) -> str:
    '''
    Extracts text from an XLSX workbook stream by reading every sheet row by row.

    Cells are joined with tabs and rows with newlines so the resulting text keeps
    a tabular shape that chunks/embeds well downstream. Fully empty rows are skipped
    and computed values are used instead of formulas.

    Parameters:
        file_stream (BytesIO): The file stream of the Excel workbook.

    Returns:
        str: Extracted text, one row per line, with a header per sheet.
    '''
    logging.info("Text extraction process started for XLSX")
    file_stream.seek(0)

    # read_only keeps memory low for large workbooks; data_only returns the last
    # computed value of formula cells rather than the formula string itself.
    workbook = load_workbook(filename=file_stream, read_only=True, data_only=True)
    extracted_parts: list[str] = []
    try:
        for sheet in workbook.worksheets:
            extracted_parts.append(f"# Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = [str(cell) for cell in row if cell is not None and str(cell).strip()]
                if cells:
                    extracted_parts.append("\t".join(cells))
            extracted_parts.append(f"\n{'-' * 50}\n")
    finally:
        workbook.close()

    return "\n".join(extracted_parts)
