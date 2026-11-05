"""Prompt for generating a structured comparison between two versions of the same document."""

_SYSTEM = (
    "You are a document analyst. Your task is to generate a detailed, structured comparison "
    "between two versions of the same document. Focus on what changed, what was added, and "
    "what was removed. Be specific and reference the relevant content."
)


def comparison_prompt(
    document_name: str,
    from_version: str | int,
    to_version: str | int,
    from_text: str,
    to_text: str,
) -> list[dict]:
    """Messages list for a one-shot comparison generation call."""
    user = (
        f'Compare these two versions of "{document_name}".\n\n'
        f"VERSION {from_version} (older):\n---\n{from_text}\n---\n\n"
        f"VERSION {to_version} (newer):\n---\n{to_text}\n---\n\n"
        f"Write a structured comparison with these sections:\n\n"
        f"## Summary\n"
        f"One paragraph overview of what changed between version {from_version} and version {to_version}.\n\n"
        f"## Additions in version {to_version}\n"
        f"Bullet list of new content, requirements, or sections added.\n\n"
        f"## Removals from version {from_version}\n"
        f"Bullet list of content, requirements, or sections that were removed.\n\n"
        f"## Modifications\n"
        f"Bullet list of content that exists in both versions but was changed, with the key difference noted.\n\n"
        f"Be specific. Quote or paraphrase relevant passages. If a section had no changes, write \"None.\""
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]
