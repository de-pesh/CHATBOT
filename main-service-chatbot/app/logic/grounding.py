"""Whether an answer's citations are actually supported by what they cite, checked once after
the answer has already been sent - app/controllers/chat_grounding.py. Params for
update_chatbot_history_grounding (scripts/mssql/07_chatbot_grounding.sql)."""

VERDICTS = ("grounded", "partial", "unsupported")
MAX_GROUNDING_DETAIL_CHARS = 500


def cited_passage_texts(results: list) -> dict[int, str]:
    """{n: passage text}, numbered the same way app.logic.prompts.build_sources numbers a
    citation's file_name/page - so a "[2]" in the answer maps to passage_texts[2] here too."""
    return {n: (hit.get("metadata") or {}).get("textContent", "") for n, hit in enumerate(results, start=1)}


def usable_grounding(data: dict) -> bool:
    return data.get("verdict") in VERDICTS


def grounding_verdict_of(data: dict) -> tuple[str, str | None]:
    """(verdict, detail) from the checker's reply - see prompts.fetch_grounding_prompt. detail is
    None for a clean "grounded" verdict with nothing to say, or when the model left it out."""
    detail = str(data.get("detail") or "").strip()[:MAX_GROUNDING_DETAIL_CHARS]
    return data["verdict"], (detail or None)


def grounding_update_params(history_id: int | str, verdict: str, detail: str | None, actor: str) -> list:
    """update_chatbot_history_grounding: [id, verdict, detail, actor]."""
    return [history_id, verdict, detail, actor]


def evaluate_answer_grounding(response: str, passages: dict[int, str]) -> dict:
    """Directly evaluates answer grounding against cited passages, returning verdict, score, and details."""
    if not response or not passages:
        return {"verdict": "grounded", "score": 1.0, "detail": "No passage citations required."}

    cited_texts = list(passages.values())
    combined_passages = " ".join(cited_texts).lower()

    # Simple overlap lexical verification metric
    words = [w.lower() for w in response.split() if len(w) > 3]
    if not words:
        return {"verdict": "grounded", "score": 1.0, "detail": "Short response."}

    matched = sum(1 for w in words if w in combined_passages)
    score = round(matched / len(words), 2)

    if score >= 0.6:
        verdict = "grounded"
        detail = None
    elif score >= 0.3:
        verdict = "partial"
        detail = "Some claims or terms in the response could not be verified in the retrieved documents."
    else:
        verdict = "unsupported"
        detail = "The answer contains claims that do not directly match retrieved document passages."

    return {"verdict": verdict, "score": score, "detail": detail}
