"""The vision model client behind ParseFile's scanned-page and image reading.

Wraps whichever vision-capable LangChain chat model api_server.py builds for it
(LiteLLM, Azure OpenAI, Azure AI Foundry, or a real Ollama server - see
config.py and parsing/vision/vision_llm_factory.py). What the model is asked lives in logic/prompts.py.
"""
import logging
from typing import Any

from app.logic.prompts import (
    IMAGE_INDEXING_PROMPT,
    PAGE_TRANSCRIPTION_PROMPT,
    consolidation_prompt,
    image_message,
    response_text,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


class VisionService:
    """Reads text and meaning out of images using an LLM.

    Two entry points, deliberately different: extract_text_from_image transcribes
    one rendered page of a scanned PDF (its output is merged with the tesseract
    layer by consolidate_text, so it must be transcription only), while
    read_image indexes a whole uploaded image and also describes it.

    `llm` is None when no vision backend is configured: every method then raises a
    clear error at the point of use rather than failing at construction, since
    every other file format parses without this.
    """

    def __init__(self, llm: BaseChatModel | None):
        self.llm = llm

    async def _ask(self, content: str | list[dict[str, Any]], label: str) -> str:
        try:
            if self.llm is None:
                raise RuntimeError(
                    "No vision LLM backend is configured (LiteLLM, Azure OpenAI, "
                    "Azure AI Foundry or Ollama - see config.py). Scanned PDFs and "
                    "image uploads need one; every other file type does not."
                )
            return response_text(await self.llm.ainvoke([HumanMessage(content=content)]))
        except Exception as e:
            # Never swallowed by returning "" or None: an empty return gets
            # uploaded as a 0-byte .txt blob and fails downstream with a
            # misleading "input_text must be a non-empty string", and a None
            # becomes the literal "None" in a page's text. Re-raised so the real
            # cause (auth, network, unsupported image, model error) surfaces.
            logger.error(f"LLM-based extraction failed in {label}: {e}")
            raise

    async def extract_text_from_image(self, image_base64: str) -> str:
        """One rendered page of a scanned PDF, transcribed as Markdown."""
        return await self._ask(image_message(PAGE_TRANSCRIPTION_PROMPT, image_base64), "extract_text_from_image")

    async def read_image(self, image_base64: str, mime: str = "image/png") -> str:
        """An image file: Markdown with a "Text in the image" and a "Description"
        section. `mime` is passed so a JPEG is not labelled as a PNG."""
        return await self._ask(image_message(IMAGE_INDEXING_PROMPT, image_base64, mime), "read_image")

    async def consolidate_text(self, ocr_text: str, img_text: str) -> str:
        """The tesseract OCR layer and the vision model's transcription, merged."""
        return await self._ask(consolidation_prompt(ocr_text, img_text), "consolidate_text")
