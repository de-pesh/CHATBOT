"""The knowledge-base catalog as data: how it is stored, rebuilt, and written into a prompt."""
import json

from app.logic.citations import short_name
from app.logic.replies import parse_json_response


def empty_catalog() -> dict:
    return {"documents": []}


def parse_catalog(raw: bytes) -> dict:
    """The stored catalog. Raises ValueError if the bytes are not one."""
    catalog = json.loads(raw.decode("utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog.get("documents"), list):
        raise ValueError("Not a knowledge-base catalog.")
    return catalog


def serialize_catalog(catalog: dict) -> bytes:
    return json.dumps(catalog, ensure_ascii=False, indent=1).encode("utf-8")


def indexed_rows(procedure_result: list) -> list:
    """The file rows that have been indexed (carry a vector_store_file_id), by name."""
    rows = [r for r in (procedure_result[0] if procedure_result else []) if r.get("vector_store_file_id")]
    return sorted(rows, key=lambda r: str(r.get("name", "")).lower())


def existing_documents(catalog: dict) -> dict:
    """The stored documents keyed by str(file_id), for reusing descriptions already written."""
    return {str(d["file_id"]): d for d in catalog.get("documents", [])}


def catalog_entry(row: dict, description: str | None) -> dict:
    """One document of the catalog; a description that could not be written is left empty."""
    return {"file_id": row["id"], "name": row["name"], "description": description or ""}


def description_from_reply(reply: str) -> str | None:
    """The one-paragraph description in a describe reply, whitespace tidied, or None if it holds none."""
    data = parse_json_response(reply)
    description = data.get("description") if isinstance(data, dict) else None
    return " ".join(description.split()) if isinstance(description, str) and description.strip() else None


def clip(text: str, limit: int) -> str:
    """`text` cut to at most `limit` characters, at the end of a sentence if there
    is one in range and otherwise at a word - never in the middle of one."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    sentence_end = head.rfind(". ")
    if sentence_end > limit // 2:
        return head[: sentence_end + 1]
    return head.rsplit(" ", 1)[0].rstrip(",;:") + "…"


def render_catalog(documents: list, max_chars: int, name_width: int) -> tuple[str, str]:
    """(the catalog as prompt text, the document names on their own, "; "-joined).

    Both are "" when there are no documents. A description that did not get
    written is left out, not invented: that document is listed by name.
    `name_width` is Settings.KB_CATALOG_NAME_WIDTH.
    """
    if not documents:
        return "", ""
    lines = []
    for doc in documents:
        name = short_name(doc["name"], name_width)
        lines.append(f"- {name}: {doc['description']}" if doc.get("description") else f"- {name}")
    head = f"Knowledge base ({len(documents)} document{'s' if len(documents) != 1 else ''}):\n"
    text = head + "\n".join(lines)
    if len(text) > max_chars:
        # Too many to describe in full: every document keeps its name, and
        # gets its description only while there is still room for the names
        # of the ones after it.
        names_only = [f"- {short_name(d['name'], name_width)}" for d in documents]
        kept, used = [], len(head)
        for i, full in enumerate(lines):
            after = sum(len(n) + 1 for n in names_only[i + 1:])
            line = full if used + len(full) + 1 + after <= max_chars else names_only[i]
            kept.append(line)
            used += len(line) + 1
        text = head + "\n".join(kept)
    return text, "; ".join(short_name(d["name"], name_width) for d in documents)


def names_a_document(reply: str, names: str, min_chars: int) -> bool:
    """Whether a reply mentions any document of the knowledge base by name.

    `names` is the "; "-joined list render_catalog returns. Names it shortened
    end in an ellipsis; the part before it is what is looked for. A name shorter
    than `min_chars` (Settings.KB_NAME_MENTION_MIN_CHARS) is ignored."""
    haystack = reply.casefold()
    for name in names.split("; "):
        name = name.strip().rstrip("….").strip().casefold()
        if len(name) >= min_chars and name in haystack:
            return True
    return False


def reusable_description(existing: dict, row: dict) -> str | None:
    """The description already written for this file, if its name has not changed since.

    `existing` maps str(file_id) to the stored catalog document.
    """
    known = existing.get(str(row["id"]))
    return known.get("description") if known and known.get("name") == row["name"] else None


def documents_changed(documents: list, existing: dict) -> bool:
    """Whether the rebuilt `documents` differ from the stored ones in `existing` (a str(file_id) -> document map)."""
    return [(d["file_id"], d["name"], d["description"]) for d in documents] != \
        [(d.get("file_id"), d.get("name"), d.get("description")) for d in existing.values()]
