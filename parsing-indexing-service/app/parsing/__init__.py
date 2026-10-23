"""Document parsing (ParsingService.ParseFile): text extraction and OCR.

    handler.py             entry point, file-type dispatch, the extension enum
    extractors.py          the extractors that need OCR, LibreOffice, Tika or a temp file
    ollama.py              vision model client, used only for images and scanned pages
    vision_llm_factory.py  picks the vision model backend from config

The pure per-format transforms are in app/logic/text_formats.py.
"""
