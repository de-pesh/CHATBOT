'''
Module: xlsx_helper

This module provides a helper function to extract text from XLSX (Excel) files.
'''

import logging
from io import BytesIO
from openpyxl import load_workbook


async def xlsx_helper(file_stream: BytesIO) -> str:
    '''
    Extracts text from an XLSX workbook stream by reading every sheet row by row.

    Cells are joined with tabs and rows with newlines so the resulting text keeps
    a tabular shape that chunks/embeds well downstream. Fully empty rows are skipped
    and computed values are used instead of formulas.

    Parameters:
        file_stream (BytesIO): The file stream of the Excel workbook.

    Returns:
        str: Extracted text, one row per line, with a header per sheet.
    '''
    logging.info("Text extraction process started for XLSX")
    file_stream.seek(0)

    # read_only keeps memory low for large workbooks; data_only returns the last
    # computed value of formula cells rather than the formula string itself.
    workbook = load_workbook(filename=file_stream, read_only=True, data_only=True)
    extracted_parts: list[str] = []
    try:
        for sheet in workbook.worksheets:
            extracted_parts.append(f"# Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = [str(cell) for cell in row if cell is not None and str(cell).strip()]
                if cells:
                    extracted_parts.append("\t".join(cells))
            extracted_parts.append(f"\n{'-' * 50}\n")
    finally:
        workbook.close()

    return "\n".join(extracted_parts)
