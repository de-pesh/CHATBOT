"""Document extractors: PyMuPDF (PDF), python-docx (DOCX/DOC), python-pptx (PPTX/PPT),
openpyxl (XLSX), BeautifulSoup (HTML), vision LLM (images), email helpers.

All rich-format extractors produce ATX-markdown output (# headings) so the
heading-based chunker in chunking.py preserves section structure.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
from pathlib import Path

from app.config import get_settings
from app.logic.text_formats import msg_text, prepare_image
from app.parsing.vision.service import VisionService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PDF — pdfplumber (text + table extraction)
# ---------------------------------------------------------------------------

def _table_to_markdown(rows: list) -> str:
    """Convert a pdfplumber table (list of rows) to a markdown table string."""
    if not rows:
        return ""
    md: list[str] = []
    for i, row in enumerate(rows):
        cells = [str(c or "").replace("\n", " ").strip() for c in row]
        md.append("| " + " | ".join(cells) + " |")
        if i == 0:
            md.append("|" + "|".join([" --- " for _ in cells]) + "|")
    return "\n".join(md)


async def pymupdf_helper(raw: bytes, vision: VisionService, suffix: str) -> str:
    """Extract text and tables from PDF using pdfplumber.

    Per page:
    - Tables are detected, extracted as markdown tables, and their bounding
      boxes are excluded from the plain-text pass so content isn't duplicated.
    - Remaining text is extracted as-is.
    Both are combined under a # Page N heading for the heading-based chunker.
    """
    import io
    import pdfplumber

    def _extract() -> str:
        parts: list[str] = []
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                page_parts: list[str] = []

                found_tables = page.find_tables()

                # Text from non-table regions (avoids duplicating table content)
                if found_tables:
                    table_bboxes = [t.bbox for t in found_tables]

                    def _outside_tables(obj, bboxes=table_bboxes):
                        for x0, top, x1, bottom in bboxes:
                            if (obj.get("x0", 0) >= x0 - 1 and
                                    obj.get("top", 0) >= top - 1 and
                                    obj.get("x1", 0) <= x1 + 1 and
                                    obj.get("bottom", 0) <= bottom + 1):
                                return False
                        return True

                    text = page.filter(_outside_tables).extract_text()
                else:
                    text = page.extract_text()

                if text and text.strip():
                    page_parts.append(text.strip())

                # Tables as markdown
                for table in found_tables:
                    md = _table_to_markdown(table.extract())
                    if md:
                        page_parts.append(md)

                if page_parts:
                    parts.append(f"# Page {i}\n\n" + "\n\n".join(page_parts))

        if not parts:
            raise ValueError("pdfplumber found no extractable text in this document.")
        return "\n\n".join(parts)

    return await asyncio.to_thread(_extract)


# ---------------------------------------------------------------------------
# DOCX / DOC — python-docx
# ---------------------------------------------------------------------------

async def docx_helper(raw: bytes, vision: VisionService, suffix: str) -> str:
    """Extract text from DOCX preserving heading styles as ATX markdown."""
    import io
    from docx import Document

    def _extract() -> str:
        doc = Document(io.BytesIO(raw))
        lines: list[str] = []

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style = para.style.name if para.style else ""
            if style.startswith("Heading 1"):
                lines.append(f"# {text}")
            elif style.startswith("Heading 2"):
                lines.append(f"## {text}")
            elif style.startswith("Heading 3"):
                lines.append(f"### {text}")
            elif style.startswith("Heading"):
                lines.append(f"#### {text}")
            else:
                lines.append(text)

        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                row_text = " | ".join(cells)
                if row_text.strip():
                    lines.append(row_text)

        if not lines:
            raise ValueError("python-docx found no extractable text in this document.")
        return "\n\n".join(lines)

    return await asyncio.to_thread(_extract)


# ---------------------------------------------------------------------------
# PPTX / PPT — python-pptx
# ---------------------------------------------------------------------------

async def pptx_helper(raw: bytes, vision: VisionService, suffix: str) -> str:
    """Extract text from PPTX with slide titles as ## headings."""
    import io
    from pptx import Presentation

    def _extract() -> str:
        prs = Presentation(io.BytesIO(raw))
        sections: list[str] = []

        for i, slide in enumerate(prs.slides, start=1):
            title: str | None = None
            body_lines: list[str] = []

            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                text = shape.text_frame.text.strip()
                if not text:
                    continue
                # idx 0 = title placeholder
                if (
                    hasattr(shape, "placeholder_format")
                    and shape.placeholder_format is not None
                    and shape.placeholder_format.idx == 0
                ):
                    title = text
                else:
                    body_lines.append(text)

            heading = f"## {title}" if title else f"## Slide {i}"
            body = "\n\n".join(body_lines)
            sections.append(f"{heading}\n\n{body}".strip() if body else heading)

        if not sections:
            raise ValueError("python-pptx found no extractable text in this document.")
        return "\n\n".join(sections)

    return await asyncio.to_thread(_extract)


# ---------------------------------------------------------------------------
# XLSX — openpyxl
# ---------------------------------------------------------------------------

async def xlsx_helper(raw: bytes, vision: VisionService, suffix: str) -> str:
    """Extract text from XLSX, one ## heading per sheet followed by rows."""
    import io
    import openpyxl

    def _extract() -> str:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        sections: list[str] = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows: list[str] = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                if any(c.strip() for c in cells):
                    rows.append(" | ".join(cells))
            if rows:
                sections.append(f"## {sheet_name}\n\n" + "\n".join(rows))

        wb.close()
        if not sections:
            raise ValueError("openpyxl found no extractable text in this document.")
        return "\n\n".join(sections)

    return await asyncio.to_thread(_extract)


# ---------------------------------------------------------------------------
# HTML / HTM — BeautifulSoup
# ---------------------------------------------------------------------------

async def html_helper(raw: bytes, vision: VisionService, suffix: str) -> str:
    """Extract readable text from HTML, mapping h1-h6 to ATX headings."""
    from bs4 import BeautifulSoup

    def _extract() -> str:
        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style", "meta", "link", "noscript"]):
            tag.decompose()

        lines: list[str] = []
        _ATX = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}

        for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th"]):
            text = el.get_text(" ", strip=True)
            if not text:
                continue
            prefix = _ATX.get(el.name, "")
            lines.append(f"{prefix} {text}" if prefix else text)

        if not lines:
            raise ValueError("BeautifulSoup found no extractable text in this document.")
        return "\n\n".join(lines)

    return await asyncio.to_thread(_extract)


# ---------------------------------------------------------------------------
# Images — vision LLM
# ---------------------------------------------------------------------------

async def image_helper(raw: bytes, vision: VisionService, suffix: str) -> str:
    """OCR + description of a standalone image via the configured vision LLM."""
    settings = get_settings()
    prepared, mime = prepare_image(raw, settings.VISION_MAX_IMAGE_SIDE, settings.IMAGE_JPEG_QUALITY)
    return await vision.read_image(base64.b64encode(prepared).decode("utf-8"), mime)


# ---------------------------------------------------------------------------
# Outlook .msg
# ---------------------------------------------------------------------------

def msg_to_text(raw: bytes) -> str:
    """Headers, body and attachment names of an Outlook .msg message."""
    try:
        import extract_msg
    except ImportError as e:
        raise RuntimeError(
            "Parsing .msg files needs the 'extract-msg' package, which is not installed."
        ) from e
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "message.msg")
        Path(path).write_bytes(raw)
        message = extract_msg.Message(path)
        try:
            return msg_text(message)
        finally:
            message.close()
