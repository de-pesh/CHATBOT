'''
Module for general utils.

This module provides a utility functions.
'''
import json
import logging
import os
import re
import uuid
from datetime import datetime
from urllib.parse import urlparse


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


def clean_string(s: str):
    """
    Removes all non-alphanumeric characters from the input string.

    Args:
        s (str): The string to clean.

    Returns:
        str: The cleaned string containing only alphanumeric characters.
    """
    return re.sub(r'[^a-zA-Z0-9]', '', s)


def stringify_json(json_data: dict):
    """
    Converts a dictionary to a JSON-formatted string.

    Args:
        json_data (dict): The dictionary to convert.

    Returns:
        str: The JSON string representation of the dictionary.
    """
    return json.dumps(json_data)


def parse_json(stringified_json: str) -> dict:
    """
    Parses a JSON-formatted string into a dictionary.

    Args:
        stringified_json (str): The JSON string to parse.

    Returns:
        dict: The parsed dictionary if successful, otherwise None.
    """
    try:
        return json.loads(stringified_json)
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON for string: {stringified_json!r}")


def get_current_microsecond_timestamp():
    """
    Returns the current UTC time in microseconds since epoch.
    """
    now = datetime.now()
    return int(now.timestamp() * 1_000_000)


def extract_blob_path_from_url(url: str) -> str:
    """
    Extracts the blob path from a given URL.

    Args:
        url (str): The URL containing the blob path.

    Returns:
        str: The extracted blob path.
    """
    # Split the URL by '/' and get the last segment
    parsed_url = urlparse(url)
    path_without_container = parsed_url.path.strip('/').split('/', 1)[1]
    return path_without_container