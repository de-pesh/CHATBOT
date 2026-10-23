"""Which model does what, and how a model's streamed reply is read."""

# The helper calls the pipeline makes (chat_steps, kb_catalog); each may run on a lighter model.
# Settings names one model per role as LLM_<ROLE>_MODEL.
HELPER_ROLES = ("rewrite", "rerank", "translate", "intent", "describe", "grounding")

# A role's model name meaning "the main model", like an empty one.
MAIN_MODEL = "main"


def helper_model_names(role_models: dict, default: str | None) -> dict:
    """{role: model name, or None for the main model}.

    A role's own setting wins, then `default` (LLM_HELPER_MODEL); "main" or
    nothing means the main model.
    """
    names = {}
    for role in HELPER_ROLES:
        name = (role_models.get(role) or default or "").strip()
        names[role] = None if name.lower() in ("", MAIN_MODEL) else name
    return names


def reasoning_efforts(helper: str | None, answer: str | None, accepted: bool) -> tuple[str | None, str | None]:
    """(helper effort, answer effort) to bind, or (None, None) for a backend whose API has no such argument
    - it would reject the call - so the setting is ignored rather than fatal."""
    return (helper or None, answer or None) if accepted else (None, None)


def chunk_text(chunk) -> str:
    """Plain text of one streamed LangChain message chunk.

    `content` is a string for Azure OpenAI and Ollama, but the chat-model interface
    also allows a list of content blocks, so both shapes are handled.
    """
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block if isinstance(block, str) else block["text"]
            for block in content
            if isinstance(block, str) or (isinstance(block, dict) and isinstance(block.get("text"), str))
        )
    return ""
