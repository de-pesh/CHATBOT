"""Per-key system-instruction overrides for the chat pipeline's prompts.

Admins write an override via PUT /prompts/{key}; the prompt builders call
get_system_override() and prepend it as an extra system message when one exists.
"""
from typing import Any

# Configurable keys — one per pipeline step that supports overrides.
PROMPT_KEYS = ("intent", "system_answer", "rewrite", "grounding", "rerank")

# The real description/template text shown to admins on GET /prompts.
# These reflect what the builders actually do, not placeholder stubs.
_REAL_TEMPLATES: dict[str, str] = {
    "intent": (
        "Classify the latest user message as one of: chitchat (small talk, greetings, off-topic), "
        "kb_overview (asks what the knowledge base covers), or knowledge (a real question to answer "
        "from documents). Output ONLY a JSON object: {\"intent\": \"<label>\", \"reply\": \"<reply if chitchat/kb_overview, else empty>\"}."
    ),
    "system_answer": (
        "Use the numbered <context> passages to answer the user's question. Cite passages inline as [n]. "
        "Answer in the same language as the question. If the question is unrelated to the context, say so "
        "and name what the knowledge base covers. Never hallucinate facts not in the passages."
    ),
    "rewrite": (
        "Rewrite the latest user question, using the conversation history for context, into 1-3 standalone "
        "search queries optimised for dense vector and BM25 retrieval. Detect the language. "
        "Output JSON: {\"queries\": [...], \"language\": \"<English|...>\", \"standalone\": \"<single best query>\"}."
    ),
    "grounding": (
        "Fact-check an assistant answer against the passages it cited. For each [n] citation, check "
        "whether passage n actually supports the claim. Output JSON: "
        "{\"verdict\": \"grounded|partial|unsupported\", \"detail\": \"<one sentence if not grounded>\"}."
    ),
    "rerank": (
        "Given a user question and a list of retrieved passages, reorder the passages from most to least "
        "relevant. Output a JSON array of passage numbers in relevance order: [3, 1, 2, ...]."
    ),
}

# Active overrides: {key: custom system instruction text}.
# Populated by PUT /prompts/{key}, cleared by POST /prompts/{key}/reset.
_OVERRIDES: dict[str, str] = {}
_VERSIONS: dict[str, int] = {k: 1 for k in PROMPT_KEYS}


def get_template(key: str) -> str:
    """The base template text for `key`, or '' if unknown."""
    return _REAL_TEMPLATES.get(key, "")


def get_system_override(key: str) -> str | None:
    """The admin-set custom instruction for this prompt, or None if not set."""
    return _OVERRIDES.get(key) or None


def set_override(key: str, template: str) -> int:
    """Store an override and increment the version. Returns the new version."""
    _OVERRIDES[key] = template
    _VERSIONS[key] = _VERSIONS.get(key, 1) + 1
    return _VERSIONS[key]


def clear_override(key: str) -> int:
    """Remove any override for `key`, resetting to the base template. Returns new version."""
    _OVERRIDES.pop(key, None)
    _VERSIONS[key] = 1
    return 1


def all_prompts() -> list[dict[str, Any]]:
    """All prompt entries as dicts, for the list endpoint."""
    return [
        {
            "key": k,
            "name": _pretty_name(k),
            "description": _descriptions[k],
            "template": _OVERRIDES.get(k) or _REAL_TEMPLATES[k],
            "version": _VERSIONS.get(k, 1),
            "has_override": k in _OVERRIDES,
        }
        for k in PROMPT_KEYS
    ]


def get_prompt(key: str) -> dict[str, Any] | None:
    if key not in PROMPT_KEYS:
        return None
    return {
        "key": key,
        "name": _pretty_name(key),
        "description": _descriptions[key],
        "template": _OVERRIDES.get(key) or _REAL_TEMPLATES[key],
        "version": _VERSIONS.get(key, 1),
        "has_override": key in _OVERRIDES,
    }


_pretty_name = {
    "intent": "Intent Classification",
    "system_answer": "Answer Generation",
    "rewrite": "Query Rewrite & Expansion",
    "grounding": "Answer Grounding Check",
    "rerank": "Passage Reranker",
}.__getitem__

_descriptions = {
    "intent": "Runs before the search. Decides whether the message is small talk, a 'what do you have?' question, or a real knowledge query.",
    "system_answer": "Builds the main answer prompt from retrieved passages. Controls citations, language, and off-topic refusals.",
    "rewrite": "Rewrites the latest question into 1-3 standalone search queries. Also detects the question's language.",
    "grounding": "After the answer is sent: checks each inline citation against its source passage and writes a verdict to the history row.",
    "rerank": "After retrieval: asks the model to reorder the retrieved passages by relevance before they are shown to the answer step.",
}
