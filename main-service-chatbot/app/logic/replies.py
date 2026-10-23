"""Reading and checking what the model sent back."""
import json
import re

from app.logic.citations import MARKER

# Both the rephrase and the translate step ask for JSON. Ollama tends to wrap it
# in ```json ... ``` fences; Azure OpenAI returns it bare.
JSON_FENCE = re.compile(r'```json\s*({.*?})\s*```', re.DOTALL)


def parse_json_response(text: str) -> dict | None:
    """Parses a model reply that is meant to be JSON, fenced or bare."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    match = JSON_FENCE.search(text or "")
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def markers(text: str) -> list:
    """The citation markers in `text`, whitespace dropped so a translation that
    respaces "[1, 3]" is not counted as changing it."""
    return sorted(re.sub(r"\s+", "", m.group(0)) for m in MARKER.finditer(text or ""))


def is_usable_translation(data: dict, original: str, must_change: bool, min_ratio: float) -> bool:
    """Whether a translate reply is an answer worth sending: the answer came back
    with every citation marker it went in with, and - when the question is known
    not to be English - actually changed. A lighter model that drops or renumbers
    a marker, or hands back the English answer as if there were nothing to
    translate, is redone rather than reaching the user. (Measured: a light model
    at minimal reasoning did the latter on 1 of 3 French runs, with valid JSON
    and every marker intact.)

    `original` is the answer that was sent for translation, stripped.
    """
    out = data.get("final_answer")
    if not isinstance(out, str) or not out.strip():
        return False
    out = out.strip()
    # The length floor is what catches a reply that is not the answer at
    # all. An answer with no citations in it has no markers to compare,
    # so without this the schema placeholder - one short sentence -
    # satisfied every other check and replaced the answer wholesale.
    if len(out) < len(original) * min_ratio:
        return False
    if markers(out) != markers(original):
        return False
    return not (must_change and out == original)


def with_reminder(messages: list, text: str) -> list:
    """`messages` with `text` appended to the last one, for a second attempt that spells the rule out."""
    return messages[:-1] + [{**messages[-1], "content": messages[-1]["content"] + text}]
