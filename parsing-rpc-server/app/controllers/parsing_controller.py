import logging
from app.config import Settings
from app.models.enums import ValidExtension
from app.services.azure_blob_storage_service import AzureBlobService
from app.services.ollama_service import OllamaService
from app.utils.general_utils import get_filename_and_extension, get_uuid
from app.utils.helpers.doc_extractor import docx_helper
from app.utils.helpers.pdf_extractor import pdf_helper
from app.utils.helpers.image_extractor import image_helper
from app.utils.helpers.txt_extractor import txt_helper
from app.utils.helpers.xlsx_extractor import xlsx_helper


async def parsing_handler(
        file_path: str,
        azure_blob_service: AzureBlobService,
        ollama_client: OllamaService
):
    settings = Settings()

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
