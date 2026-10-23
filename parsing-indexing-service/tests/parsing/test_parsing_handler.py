"""parsing_handler dispatch, with an in-memory store and mocked extractors."""
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from app.parsing.handler import parsing_handler
from app.parsing.vision.service import VisionService


class MemoryStore:
    def __init__(self, files):
        self.files, self.uploaded = files, {}

    async def exists(self, path):
        return path in self.files

    async def download_file(self, path):
        return BytesIO(self.files[path])

    async def upload_text(self, path, text):
        self.uploaded[path] = text
        return path


def png() -> bytes:
    from PIL import Image
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


_FAKE_MD = "# Section\n\nsome extracted text"


def _mock_extractor(text: str = _FAKE_MD):
    """Return an AsyncMock that stands in for any async extractor helper."""
    return AsyncMock(return_value=text)


# ---------------------------------------------------------------------------
# Format routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, raw, ends_with, check_content", [
    ("a.txt",  b"plain words",      ".txt", "plain words"),
    ("a.eml",  b"Subject: S\n\neml words\n", ".txt", "eml words"),
    ("a.pdf",  b"pdfbytes",         ".md",  "extracted text"),
    ("a.docx", b"docxbytes",        ".md",  "extracted text"),
    ("a.doc",  b"docbytes",         ".md",  "extracted text"),
    ("a.pptx", b"pptxbytes",        ".md",  "extracted text"),
    ("a.ppt",  b"pptbytes",         ".md",  "extracted text"),
    ("a.xlsx", b"xlsxbytes",        ".md",  "extracted text"),
    ("a.html", b"<p>web words</p>", ".md",  "extracted text"),
    ("dir/UPPER.TXT", b"case ok",   ".txt",  "case ok"),
], ids=["txt", "eml", "pdf", "docx", "doc", "pptx", "ppt", "xlsx", "html", "uppercase"])
async def test_each_format_is_extracted_and_written_back_beside_the_original(
    name, raw, ends_with, check_content, monkeypatch
):
    import app.parsing.extractors as extractors
    for fn in ("pymupdf_helper", "docx_helper", "pptx_helper", "xlsx_helper", "html_helper"):
        monkeypatch.setattr(extractors, fn, _mock_extractor())
    store = MemoryStore({name: raw})
    blob_path = await parsing_handler(name, store, VisionService(None))
    assert blob_path.endswith(ends_with) and "/texts/" in f"/{blob_path}"
    assert check_content in store.uploaded[blob_path]


async def test_a_missing_file_and_an_unsupported_extension_are_refused():
    store = MemoryStore({"a.xyz": b"x"})
    with pytest.raises(FileNotFoundError):
        await parsing_handler("nope.txt", store, VisionService(None))
    with pytest.raises(ValueError, match="not supported"):
        await parsing_handler("a.xyz", store, VisionService(None))


async def test_a_file_with_no_text_is_an_error_not_an_empty_upload():
    store = MemoryStore({"a.txt": b"   \n "})
    with pytest.raises(ValueError, match="No text could be extracted"):
        await parsing_handler("a.txt", store, VisionService(None))
    assert store.uploaded == {}


# ---------------------------------------------------------------------------
# Image routing: still goes to the vision LLM
# ---------------------------------------------------------------------------

class ScriptedLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    async def ainvoke(self, messages):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        from types import SimpleNamespace
        return SimpleNamespace(content=reply)


async def test_an_image_is_read_by_the_vision_model():
    store = MemoryStore({"a.png": png()})
    blob_path = await parsing_handler("a.png", store, VisionService(ScriptedLLM("## Text\nhi")))
    assert "hi" in store.uploaded[blob_path]
    assert blob_path.endswith(".md")


async def test_an_image_without_a_vision_backend_fails_with_the_reason():
    with pytest.raises(RuntimeError, match="No vision LLM backend"):
        await parsing_handler("a.png", MemoryStore({"a.png": png()}), VisionService(None))


# ---------------------------------------------------------------------------
# Output path: extracted text is written beside the original
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source, prefix", [
    ("dir/sub/a.txt", "dir/sub/texts/"),
    ("a.txt",         "texts/"),
])
async def test_the_extracted_text_is_written_under_the_originals_own_directory(source, prefix):
    store = MemoryStore({source: b"words"})
    blob_path = await parsing_handler(source, store, VisionService(None))
    assert blob_path.startswith(prefix) and blob_path.endswith(".txt") and blob_path.count("/") == prefix.count("/")


# ---------------------------------------------------------------------------
# Blank-page guard: mostly-blank documents are refused when a limit is set
# ---------------------------------------------------------------------------

def _pages(*texts) -> bytes:
    from app.logic.text_formats import PAGE_BREAK
    return "".join(text + PAGE_BREAK for text in texts).encode()


async def test_a_document_that_is_mostly_blank_pages_is_refused_over_the_limit():
    store = MemoryStore({"scan.txt": _pages("real words", " ", " ", " ")})
    with pytest.raises(ValueError, match="1 of 4 pages"):
        await parsing_handler("scan.txt", store, VisionService(None), max_blank_page_share=0.5)
    assert store.uploaded == {}


async def test_blank_pages_are_tolerated_up_to_the_limit_and_by_default():
    raw = _pages("real words", "more words", " ")
    assert await parsing_handler("a.txt", MemoryStore({"a.txt": raw}), VisionService(None), max_blank_page_share=0.5)
    mostly_blank = _pages("real words", " ", " ")
    assert await parsing_handler("b.txt", MemoryStore({"b.txt": mostly_blank}), VisionService(None))
