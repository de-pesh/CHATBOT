"""Small shared helpers.

Was a four-module package - general_utils, json_utils, string_utils, time_utils
- totalling 63 lines between them, with a single importer. string_utils had no
importer at all and its one function is gone.
"""
import json
import uuid
from datetime import datetime


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


def get_current_microsecond_timestamp():
    """
    Returns the current UTC time in microseconds since epoch.
    """
    # Returns the current UTC time in microseconds.
    now = datetime.now()
    return int(now.timestamp() * 1_000_000)
def get_currect_timestamp():
    # Returns the current timestamp in a specific format.
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stringify_json(data):
    """
    Converts a Python object to a JSON formatted string.

    Args:
        data: The Python object to convert.

    Returns:
        A JSON formatted string representation of the object.
        Returns an empty string if the input data is empty.
        Returns the string representation of the data if it's not JSON serializable.
    """
    if not data:
        return ""
    try:
        return json.dumps(data, indent=2)
    except TypeError:
        return str(data)