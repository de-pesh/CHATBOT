'''
Module for general utils.

This module provides a utility functions.
'''
import json
import logging
import os
import re
import uuid


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
    return re.sub(r'[^a-zA-Z0-9]', '', s)


def stringify_json(json_data: dict):
    return json.dumps(json_data)


def parse_json(stringified_json: str) -> dict:
    try:
        return json.loads(stringified_json)
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON for string: {stringified_json!r}")
