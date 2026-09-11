'''
Module: image_ocr_llm_extractor

This module provides a wrapper around LangChain Ollama LLM.
It extracts images from PDF pages and sends them to an LLM for text extraction.
'''

from langchain_azure_ai.chat_models import AzureAIChatCompletionsModel
from langchain_core.messages import HumanMessage
import logging


class OllamaService:
    '''
    A wrapper class that extracts text from PDF images using an LLM.

    This class converts each page of a PDF into an image, encodes it, and sends it
    to a local Ollama LLM instance for text extraction.
    '''

    def __init__(
            self,
            model: str,
            base_url: str, 
            api_key: str,
            temperature: float = 0.1,
            api_version: str = "2024-05-01-preview"):
        '''
        Initializes the extractor with an LLM client.

        Parameters:
            model (str): Name of the Ollama model to use.
            base_url (str): Base URL of the Ollama server.
            temperature (float): LLM response temperature.
        '''
        # self.llm = ChatOllama(
        #     model=model,
        #     base_url=base_url,
        #     temperature=temperature
        # )
        if not api_key:
            raise ValueError("Missing API key. Set OLLAMA_API_KEY in your environment.")

        self.llm = AzureAIChatCompletionsModel(
            endpoint=base_url,
            credential=api_key,
            model=model,
            api_version=api_version,
            temperature=temperature,
            # max_tokens=2048
        )

    async def extract_text_from_image(self, image_base64: str) -> list[str]:
        '''
        Extracts text from a PDF using OCR and LLM image understanding.

        Parameters:
            image_base64 (str): Base64 of an image.

        Returns:
            list[str]: Extracted text content from each page.
        '''

        try:
            # Prepare LLM message
            message = HumanMessage(content=[
                {
                    "type": "text",
                    "text": (
                        "Extract all text from the image and return it as markdown."
                        "\nDo not describe the image or add extra text. "
                        "\nOnly return the text found in the image."
                    )
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"}
                }
            ])

            # Get response from LLM
            response = await self.llm.ainvoke([message])
            return (response.content)

        except Exception as e:
            # Do NOT swallow the failure by returning "". An empty return value
            # gets uploaded as a 0-byte .txt blob and then fails downstream in
            # the indexing service with a misleading
            # "input_text must be a non-empty string" error. Re-raise so the
            # real cause (auth, network, unsupported image, model error) surfaces.
            logging.error(f"LLM-based extraction failed in extract_text_from_image: {str(e)}")
            raise
        
    async def consolidate_text(self, ocr_text:str, img_text:str) -> list[str]:
        '''
        Extracts text from a PDF by combining text extracted from OCR and LLM.

        Parameters:
            ocr_text (str): Text extracted using OCR.
            img_text(str): Text extracted using LLm

        Returns:
            list[str]: Extracted text content from each page.
        '''

        try:
            system_prompt = f"""     
            You are provided with two different OCR outputs extracted from the same page:

            1. <Version 1>: Text extracted from Service 1
            2. <Version 2>: Text extracted from Service 2

            ---<Version 1>---
            {ocr_text}
            ---</Version 1>---

            ---<Version 2>---
            {img_text}
            ---</Version 2>---

            Your task is to generate a clean, merged version of the page content that:

            - Primarily relies on **Version 1** for accurate textual data.
            - Uses **Version 2** to enhance **formatting** (e.g., tables, spacing, alignment, structure).
            - Avoids duplicating overlapping or misspelled segments.
            - Only includes text from **Version 2** if it contains **genuinely missing** content that is not present in **Version 1**.
            - Preserves logical reading order and document integrity.

            Focus on producing a human-readable output that is faithful to the original document in both content and structure.

            Return only the merged and formatted page text. Do not explain your reasoning.
            """
            # Prepare LLM message
            message = HumanMessage(content=system_prompt)

            # Get response from LLM
            response = self.llm.invoke([message])
            return (response.content)

        except Exception as e:
            logging.error("LLM-based extraction failed")
            return 
