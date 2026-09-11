'''
Module for general utils.

This module provides a utility functions.
'''
import json
import logging
import os
import re
import uuid


def parse_json(stringified_json: str) -> dict:
    """
    Parse a JSON-formatted string into a Python dictionary.

    Args:
        stringified_json (str): The JSON string to parse.

    Returns:
        dict: The parsed JSON as a dictionary, or None if parsing fails.

    Logs:
        An error message if the JSON string cannot be decoded.
    """
    try:
        return json.loads(stringified_json)
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON for string: {stringified_json!r}")
