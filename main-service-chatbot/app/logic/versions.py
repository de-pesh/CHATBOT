"""Knowledge-base document versions: which uploads are versions of one document, and which versions a question wants.

A document is identified by its **document key**: the file name with its extension and any trailing version
tag ("_v3.2") removed, lower-cased. Uploading a file whose key already exists makes the next version of that
document; the newest ingested version is the **latest**. Older versions stay searchable but only when a question
asks for them (see `detect_scope`); every other question is answered from the latest versions alone.

Pure functions: no database, no search. The chunk metadata they read is written at ingestion (kb_jobs.index_metadata):
`version_no`, `version_label`, `is_latest`.
"""
import re
from dataclasses import dataclass
from enum import StrEnum

# A trailing version tag on a file name: "Policy_v3.2", "Policy v2", "Policy-v10".
_NAME_VERSION = re.compile(r"[\s._-]*v(\d+(?:\.\d+)*)$", re.IGNORECASE)
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")


# A version label is shown in prompts, citations and the browser, so it is limited to plain characters.
VERSION_LABEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$"
_LABEL = re.compile(VERSION_LABEL_PATTERN)


def clean_label(label: str | None) -> str | None:
    """The label stripped, or None for an empty one. Raises ValueError for one with characters a prompt or a
    page should not be handed ("3.2", "draft-1" and "2026_Q1" are fine)."""
    label = (label or "").strip()
    if not label:
        return None
    if not _LABEL.match(label):
        raise ValueError("A version label is letters, digits, '.', '_' or '-', at most 50 characters.")
    return label


def split_document_name(file_name: str) -> tuple[str, str | None]:
    """(document key, version label from the name or None): "Info Sec Policy_v3.2.pdf" -> ("info sec policy", "3.2")."""
    stem = _EXTENSION.sub("", (file_name or "").strip())
    label, key_stem = None, stem
    if match := _NAME_VERSION.search(stem):
        label, key_stem = match.group(1), stem[:match.start()]
    key = " ".join((key_stem or stem).lower().split())  # a file named only "v2.pdf" keeps its name as its key
    return key, (label if key_stem else None)


class Mode(StrEnum):
    LATEST = "latest"      # the default: the newest version of every document
    PREVIOUS = "previous"  # only older versions ("what did the previous version say")
    ALL = "all"            # every version, for a comparison
    SPECIFIC = "specific"  # one named version ("version 2", "v3.2")


@dataclass(frozen=True)
class VersionScope:
    mode: Mode = Mode.LATEST
    token: str | None = None  # the version number or label a SPECIFIC scope names

    @property
    def wants_older_versions(self) -> bool:
        return self.mode is not Mode.LATEST

    def matches(self, metadata: dict) -> bool:
        """Whether a chunk with this metadata belongs to the scope. A chunk from before versioning has no
        version fields: it counts as the latest, and as version 1."""
        is_latest = metadata.get("is_latest") is not False
        if self.mode is Mode.LATEST:
            return is_latest
        if self.mode is Mode.ALL:
            return True
        if self.mode is Mode.PREVIOUS:
            return not is_latest
        number = metadata.get("version_no")
        number = "1" if number is None else str(int(number))
        return self.token in (number, str(metadata.get("version_label") or ""))


LATEST = VersionScope()

_COMPARE = re.compile(
    r"\bcompar(?:e|es|ed|ing|ison)\b|\bdifferences?\s+between\b|\bwhat\s+(?:has\s+|have\s+)?changed\b"
    r"|\bchanges?\s+(?:between|from|since|made)\b|\bvs\.?\s|\bversus\b|\bdiff(?:erence)?\s+(?:of|in|for)\b",
    re.IGNORECASE,
)
_PREVIOUS = re.compile(
    r"\b(?:previous|prior|older|earlier|old|last|former|superseded)\s+(?:version|revision|edition|release)s?\b"
    r"|\b(?:version|revision|edition)s?\s+before\b|\bused\s+to\s+(?:say|be|require)\b|\bwas\s+previously\b",
    re.IGNORECASE,
)
_SPECIFIC = re.compile(r"\b(?:version|ver|revision|rev|v)\.?\s*(\d+(?:\.\d+)*)\b", re.IGNORECASE)


def detect_scope(question: str) -> VersionScope:
    """Which versions a question is asking about, from its wording. Deterministic and testable; no model call.

    - "compare ...", "what changed", two different versions named   -> ALL versions
    - "the previous / older version ..."                             -> only older versions
    - "version 2", "v3.2"                                            -> that version
    - anything else, "current" / "latest" included                   -> the latest versions
    """
    text = question or ""
    named = list(dict.fromkeys(match.group(1) for match in _SPECIFIC.finditer(text)))
    if _COMPARE.search(text) or len(named) > 1:
        return VersionScope(Mode.ALL)
    if _PREVIOUS.search(text):
        return VersionScope(Mode.PREVIOUS)
    if named:
        return VersionScope(Mode.SPECIFIC, named[0])
    return LATEST


def version_tag(metadata: dict) -> str:
    """"v2" or "v3.2" for a chunk, or "" for one from before versioning."""
    label = metadata.get("version_label")
    if label:
        return f"v{label}"
    number = metadata.get("version_no")
    return f"v{int(number)}" if number is not None else ""


def is_older(metadata: dict) -> bool:
    """Whether a chunk belongs to a version that a newer one has replaced."""
    return metadata.get("is_latest") is False and metadata.get("version_no") is not None
