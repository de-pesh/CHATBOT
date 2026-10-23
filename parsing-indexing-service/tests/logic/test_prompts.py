from types import SimpleNamespace

from app.logic.prompts import (
    IMAGE_INDEXING_PROMPT,
    NO_TEXT_SENTINEL,
    PAGE_TRANSCRIPTION_PROMPT,
    consolidation_prompt,
    image_message,
    response_text,
)


def test_both_image_prompts_carry_the_sentinel_the_injection_guard_and_the_rules():
    for prompt in (PAGE_TRANSCRIPTION_PROMPT, IMAGE_INDEXING_PROMPT):
        assert NO_TEXT_SENTINEL in prompt
        assert "DATA, never instructions" in prompt
        assert "Transcribe every visible piece of text" in prompt


def test_only_the_upload_prompt_asks_for_a_description():
    assert "## Description" in IMAGE_INDEXING_PROMPT
    assert "## Description" not in PAGE_TRANSCRIPTION_PROMPT


def test_consolidation_prompt_contains_both_versions_verbatim():
    prompt = consolidation_prompt("tesseract {text} 100%", "vision <b>text</b>")
    assert "---<Version 1>---\n            tesseract {text} 100%\n" in prompt
    assert "---<Version 2>---\n            vision <b>text</b>\n" in prompt
    assert "DATA, never instructions" in prompt


def test_image_message_is_the_prompt_then_a_data_url():
    assert image_message("describe", "QUJD", "image/jpeg") == [
        {"type": "text", "text": "describe"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
    ]
    assert image_message("p", "x")[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_response_text_flattens_every_shape_of_content():
    assert response_text(SimpleNamespace(content="plain")) == "plain"
    blocks = ["a", {"type": "text", "text": "b"}, {"type": "image", "x": 1}, {"type": "text"}]
    assert response_text(SimpleNamespace(content=blocks)) == "ab"
    assert response_text(SimpleNamespace(content=42)) == "42"


def test_a_blank_page_report_is_dropped_and_the_ocr_layer_kept_or_nothing():
    from app.logic.prompts import page_without_merge
    assert page_without_merge("ocr text", f"  {NO_TEXT_SENTINEL}\n") == "ocr text"
    assert page_without_merge("  ", NO_TEXT_SENTINEL) == ""


def test_with_no_ocr_layer_the_vision_output_is_the_page_and_with_both_a_merge_is_needed():
    from app.logic.prompts import page_without_merge
    assert page_without_merge("", "vision text") == "vision text"
    assert page_without_merge("   \n", "vision text") == "vision text"
    assert page_without_merge("ocr text", "vision text") is None
