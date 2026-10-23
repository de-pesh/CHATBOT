"""Vision service tests (the model client, prompts, and error paths)."""
from types import SimpleNamespace

import pytest
from app.parsing.vision.service import VisionService


class FakeLLM:
    def __init__(self, reply="answer", error=None):
        self.reply, self.error, self.calls = reply, error, []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.reply)


async def test_extract_text_from_image_sends_the_prompt_and_the_page_image():
    llm = FakeLLM("# Page")
    assert await VisionService(llm).extract_text_from_image("QUJD") == "# Page"
    text_block, image_block = llm.calls[0][0].content
    assert "transcribing one page" in text_block["text"]
    assert image_block["image_url"]["url"] == "data:image/png;base64,QUJD"


async def test_read_image_labels_the_image_with_its_own_media_type():
    llm = FakeLLM()
    await VisionService(llm).read_image("QUJD", "image/jpeg")
    assert llm.calls[0][0].content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_consolidate_text_sends_both_versions_as_a_plain_string():
    llm = FakeLLM("merged")
    assert await VisionService(llm).consolidate_text("from tesseract", "from vision") == "merged"
    content = llm.calls[0][0].content
    assert "from tesseract" in content and "from vision" in content


async def test_a_missing_backend_is_a_clear_error_at_the_point_of_use():
    with pytest.raises(RuntimeError, match="No vision LLM backend is configured"):
        await VisionService(None).read_image("QUJD")


async def test_a_model_failure_is_raised_not_swallowed(caplog):
    with pytest.raises(ConnectionError):
        await VisionService(FakeLLM(error=ConnectionError("down"))).extract_text_from_image("QUJD")
    assert "extract_text_from_image" in caplog.text and "down" in caplog.text
