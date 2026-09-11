import os
import base64
import logging
from io import BytesIO
import fitz
from PIL import Image
import tempfile
import ocrmypdf
from app.services.ollama_service import OllamaService

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