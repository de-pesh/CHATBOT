"""
This module provides utilities for generating secure, time-limited SAS URLs for Azure Blob Storage 
and for embedding clickable links into chat citations based on recognized file references.

Functions:
    - generate_sas_url: Generates a secure SAS URL for a given blob.
    - linkify_citations: Replaces file mentions in response text with clickable links using SAS URLs.
"""
import re
from datetime import datetime, timedelta
from urllib.parse import quote
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from app.config import get_settings

settings = get_settings()

pdf_url_map = {
    "CYBER INCIDENT RESPONSE FRAMEWORK.pdf": "GENAI/CPX/uploads/policy_pdfs/CYBER INCIDENT RESPONSE FRAMEWORK.pdf",
    "CYBER INCIDENT RESPONSE PLAN.pdf": "GENAI/CPX/uploads/policy_pdfs/CYBER INCIDENT RESPONSE PLAN.pdf",
    "CYBER SECURITY INFORMATION SHARING FRAMEWORK.pdf": "GENAI/CPX/uploads/policy_pdfs/CYBER SECURITY INFORMATION SHARING FRAMEWORK.pdf",
    "NATIONAL CLOUD SECURITY POLICY.pdf": "GENAI/CPX/uploads/policy_pdfs/NATIONAL CLOUD SECURITY POLICY.pdf",
    "NATIONAL IoT SECURITY POLICY.pdf": "GENAI/CPX/uploads/policy_pdfs/NATIONAL IoT SECURITY POLICY.pdf",
    "CRITICAL INFORMATION INFRASTRUCTURE PROTECTION (CIIP) POLICY.pdf": "GENAI/CPX/uploads/policy_pdfs/CRITICAL INFORMATION INFRASTRUCTURE PROTECTION (CIIP) POLICY.pdf",
}

def generate_sas_url(blob_name: str) -> str:
    """
    Generate a secure, time-limited SAS URL for a blob in Azure Storage.

    Args:
        blob_name (str): The name (path) of the blob within the storage container.

    Returns:
        str: A full URL including SAS token, valid for 1 hour.
    
    Raises:
        Exception: If SAS generation fails due to misconfiguration or Azure issues.
    """

    account_name = settings.AZURE_STORAGE_ACCOUNT_NAME
    account_key = settings.AZURE_STORAGE_ACCOUNT_KEY
    container_name = settings.AZURE_STORAGE_CONTAINER_NAME

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.utcnow() + timedelta(hours=1)
    )

    encoded_blob_name = quote(blob_name, safe='')
    return f"https://{account_name}.blob.core.windows.net/{container_name}/{encoded_blob_name}?{sas_token}"


def linkify_citations(response: str) -> str:
    """
    Replace citation references in a response string with clickable HTML links to documents.

    Parses lines following a "Cited:" marker or bullet points, attempts to map filenames 
    to known Azure blobs, and replaces file names with hyperlinks that open the corresponding 
    document via a SAS URL.

    Args:
        response (str): The original text response containing citations.

    Returns:
        str: The modified response with embedded HTML links for citations.

    Notes:
        - If a citation file name is not found in `pdf_url_map`, the original text is left unchanged.
        - If SAS URL generation fails for a specific blob, it logs an error and leaves the line unchanged.
    """
    lines = response.strip().splitlines()
    new_lines = []

    in_cited_block = False

    for line in lines:
        stripped = line.strip()

        if stripped == "Cited:":
            new_lines.append("Cited:")
            in_cited_block = True
            continue

        if in_cited_block and stripped.startswith("- "):
            match = re.match(r"-\s*\**(.+?)\**,\s*Page[:\s]*(.+)", stripped)
            if match:
                file_name, page_info = match.groups()
                file_name = file_name.strip()
                blob_name = (
                    pdf_url_map.get(file_name) or
                    pdf_url_map.get(f"{file_name}.pdf") or
                    pdf_url_map.get(file_name.replace(".pdf", "").strip())
                )

                if blob_name:
                    try:
                        blob_url = generate_sas_url(blob_name)
                        link = f"<a target='_blank' style='color:#eed202' href='{blob_url}'>{file_name}</a>"
                        new_lines.append(f"- {link}, Page: {page_info.strip()}")
                    except Exception as e:
                        print(f"Failed to generate SAS for {file_name}: {e}")
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        elif stripped.startswith("Cited:"):
            match = re.match(r"Cited:\s*\**(.+?)\**,\s*Page[:\s]*(.+)", stripped)
            if match:
                file_name, page_info = match.groups()
                file_name = file_name.strip()
                blob_name = (
                    pdf_url_map.get(file_name) or
                    pdf_url_map.get(f"{file_name}.pdf") or
                    pdf_url_map.get(file_name.replace(".pdf", "").strip())
                )

                if blob_name:
                    try:
                        blob_url = generate_sas_url(blob_name)
                        link = f"<a target='_blank' style='color:#eed202' href='{blob_url}'>{file_name}</a>"
                        new_lines.append(f"Cited: {link}, Page: {page_info.strip()}")
                    except Exception as e:
                        print(f"Failed to generate SAS for {file_name}: {e}")
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        else:
            in_cited_block = False
            new_lines.append(line)

    return "\n".join(new_lines)
