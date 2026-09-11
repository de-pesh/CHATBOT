"""Document parsing entry point.

Was three modules - a controller, a one-enum models package and a utils package
- across three directories. Everything still used by the handler lives here.
"""
import json
import logging
import os
import uuid
from enum import Enum

from app.parsing.blob import AzureBlobService
from app.parsing.extractors import (
    docx_helper,
    image_helper,
    pdf_helper,
    txt_helper,
    xlsx_helper,
)
from app.parsing.ollama import OllamaService

class ValidExtension(Enum):
    '''
    Enum representing valid file extensions.

    Attributes:
        PDF (str): Represents '.pdf' extension.
        WORD (tuple): Represents '.docx' and '.doc' extensions.
        IMAGE (tuple): Represents '.png', '.jpeg' and '.jpg' extensions.
        TEXT (str): Represents '.txt' extension.
        EXCEL (str): Represents '.xlsx' extension.
    '''
    PDF = ".pdf"
    WORD = (".docx", ".doc")
    IMAGE = (".png", ".jpeg", ".jpg")
    TEXT = ".txt"
    EXCEL = ".xlsx"


    @classmethod
    def is_valid(cls, extension: str) -> bool:
        '''
        Checks if a given file extension is valid.

        Parameters:
            extension (str): The file extension to validate.

        Returns:
            bool: True if the extension is valid, False otherwise.
        '''
        for value in cls:
            if isinstance(value.value, str):
                if extension == value.value:
                    return True
            elif isinstance(value.value, (list, tuple)):
                if extension in value.value:
                    return True
        return False
        # return extension.lower() in cls.__members__.values()


def get_uuid():
    '''
    Generates a new UUID and returns it as a string.

    Parameters:
        None

    Returns:
        str: A newly generated UUID in string format.
    '''
    # Generate a UUID using uuid4, which is randomly generated.
    return str(uuid.uuid4())


def get_filename_and_extension(file_path: str) -> tuple[str, str]:
    """
    Extract the filename and extension from a given file path.

    Parameters:
        file_path (str): The full path to the file.

    Returns:
        tuple[str, str]: A tuple containing the filename and the file extension.
    """
    # Get basename from complete file path.
    basename = os.path.basename(file_path)

    # Split filename and extension.
    filename, ext = os.path.splitext(basename)

    # Clean extension.
    ext = ext.lower()

    # Return filename and extension as packed value
    return (filename, ext)


def parse_json(stringified_json: str) -> dict:
    try:
        return json.loads(stringified_json)
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON for string: {stringified_json!r}")


async def parsing_handler(
        file_path: str,
        azure_blob_service: AzureBlobService,
        ollama_client: OllamaService
):

    # Check if the file exists in Azure Blob Storage
    if not await azure_blob_service.check_blob_exist(file_path):
        logging.error(f"blob '{file_path}' does not exist, skipping this message")

    else:
        print(f"blob '{file_path}' exists, proceeding with parsing")

    # Extract the filename and extension from the file path
    filename, ext = get_filename_and_extension(
        file_path=file_path
    )
    print(f"Extracted filename: '{filename}', extension: '{ext}'")

    # Reject unsupported file types early so we never fall through without data.
    if not ValidExtension.is_valid(ext):
        message = f"File with extension '{ext}' is not supported right now."
        logging.error(message)
        raise ValueError(message)

    # Handle PDF files
    if ext == ValidExtension.PDF.value:

        # Download the file as a stream from Azure Blob Storage
        file_stream = await azure_blob_service.download_blob_stream(blob_path=file_path)

        # Extract text from the PDF file
        extracted_data = await pdf_helper(
            file_stream=file_stream,
            ollama_client=ollama_client,
        )

    # Handle Word documents (.docx, .doc)
    elif ext in ValidExtension.WORD.value:

        # Download the file as a stream from Azure Blob Storage
        file_stream = await azure_blob_service.download_blob_stream(blob_path=file_path)

        # Extract text from the Word document
        extracted_data = await docx_helper(
            file_stream=file_stream,
            ollama_client=ollama_client,
            suffix=ext,
        )

    # Handle Images
    elif ext in ValidExtension.IMAGE.value:
        # Download the file as a stream from Azure Blob Storage
        file_stream = await azure_blob_service.download_blob_stream(blob_path=file_path)

        extracted_data = await image_helper(
            file_stream=file_stream,
            ollama_client=ollama_client,
        )

    # Handle plain text files (.txt)
    elif ext == ValidExtension.TEXT.value:
        # Download the file as a stream from Azure Blob Storage
        file_stream = await azure_blob_service.download_blob_stream(blob_path=file_path)

        # Plain text needs no LLM/OCR; decode the bytes directly.
        extracted_data = await txt_helper(file_stream=file_stream)

    # Handle Excel workbooks (.xlsx)
    elif ext == ValidExtension.EXCEL.value:
        # Download the file as a stream from Azure Blob Storage
        file_stream = await azure_blob_service.download_blob_stream(blob_path=file_path)

        # Flatten every sheet/row into tab-delimited text.
        extracted_data = await xlsx_helper(file_stream=file_stream)

    # Guard against empty extractions. Uploading an empty string creates a
    # 0-byte .txt blob that later crashes the indexing service with a
    # misleading "input_text must be a non-empty string" error. Fail here with
    # a clear, actionable message at the correct layer instead.
    if not isinstance(extracted_data, str) or not extracted_data.strip():
        message = (
            f"No text could be extracted from '{file_path}' (extension '{ext}'). "
            "The file may be empty, an image/scan without readable text, or the "
            "extraction step returned no content."
        )
        logging.error(message)
        raise ValueError(message)

    # Create a unique path from the original file path
    base_path = ''.join(file_path.rsplit(sep='/', maxsplit=2)[0])
    blob_path = f"{base_path}/texts/{get_uuid()}.txt"

    # Upload the text data to Azure Blob Storage
    await azure_blob_service.upload_text_to_blob(
        blob_path=blob_path,
        file_data=extracted_data
    )

    return blob_path
