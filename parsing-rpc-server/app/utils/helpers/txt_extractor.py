'''
Module: txt_helper

This module provides a helper function to extract text from plain text (.txt) files.
'''

import logging
from io import BytesIO


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
