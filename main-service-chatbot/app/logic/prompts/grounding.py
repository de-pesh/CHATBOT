"""After the answer: the prompt that checks its citations against the passages."""

import json

from app.logic.prompt_registry import get_system_override


def fetch_grounding_prompt(response: str, passage_texts: dict, passage_chars: int) -> list:
    """
    Asks the model to check, after the fact, whether `response`'s [n] citations are actually
    supported by the passage each one points at - see app/controllers/chat_grounding.py, which
    calls this once the answer has already been sent to the browser (Settings.GROUNDING_CHECK_ENABLED).

    `passage_texts` is app.logic.grounding.cited_passage_texts(passages): {n: passage text},
    numbered the same way build_sources numbers the citations the answer itself refers to, so a
    "[2]" in `response` is checked against passage_texts[2].
    """
    passages = "\n".join(
        f"[{n}]: {json.dumps(text[:passage_chars], ensure_ascii=False)}"
        for n, text in sorted(passage_texts.items())
    )
    prompt = f"""You are fact-checking an assistant's answer against the passages it was allowed to cite.

    Passages:
    {passages}

    Answer:
    {json.dumps(response, ensure_ascii=False)}

    The answer cites passages by [n]. For every claim that carries a citation, check whether the
    passage with that number actually supports it. A factual claim with no citation at all also
    counts against the answer; a greeting, an apology, or "I don't know" does not.

    Return a verdict:
    - "grounded": every citation is supported, and no material factual claim is uncited.
    - "partial": most of the answer is supported, but at least one claim is unsupported,
      cites the wrong passage, or is missing a citation.
    - "unsupported": the answer is largely not backed by the passages.

    Output strictly in JSON like:
    ```json
    {{"verdict": "grounded", "detail": "one short sentence naming the problem, empty if grounded"}}
    ```"""
    messages = []
    if override := get_system_override("grounding"):
        messages.append({"role": "system", "content": override})
    messages.append({"role": "user", "content": prompt})
    return messages
