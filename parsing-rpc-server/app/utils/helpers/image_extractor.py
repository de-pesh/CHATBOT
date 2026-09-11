import base64
from io import BytesIO
import logging
from app.config import get_settings
from app.services.ollama_service import OllamaService
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