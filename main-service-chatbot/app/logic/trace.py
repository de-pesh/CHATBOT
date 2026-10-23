"""What one chat turn did, kept so a bad answer can be explained afterwards: the queries it searched,
what came back, what survived the rerank, whether the evidence gate stopped it, and which model and
configuration it ran on. Params for upsert_chatbot_trace (scripts/mssql/09_audit_trace_hash.sql).

The trace is bookkeeping: building or saving it must never fail a turn (see routers/chat.py)."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from app.logic import prompts

OUTCOME_ANSWERED = "answered"
OUTCOME_NO_EVIDENCE = "no_evidence"

# The settings that change what an answer can be. A trace records a hash of them, so two turns with
# the same config_version ran the same model, the same search and the same thresholds.
_CONFIG_FIELDS = (
    "LLM_TYPE", "LITELLM_MODEL_NAME", "LLM_HELPER_MODEL", "LLM_REWRITE_MODEL", "LLM_RERANK_MODEL",
    "LLM_TRANSLATE_MODEL", "LLM_INTENT_MODEL", "LLM_FALLBACK_MODEL", "SEARCH_RESULT_LIMIT", "RERANK_TOP_K",
    "RERANK_MAX_CANDIDATES", "QUERY_FUSION_RRF_K", "MIN_RELEVANCE_SCORE", "NO_EVIDENCE_GATE_ENABLED",
    "REWRITE_MAX_QUERIES", "REWRITE_EXPANSION_BUDGET", "GROUNDING_CHECK_ENABLED",
)


@lru_cache(maxsize=1)
def prompt_version() -> str:
    """A short hash of the prompt package's source, so editing any prompt changes the version."""
    digest = hashlib.sha256()
    for source in sorted(Path(prompts.__file__).parent.glob("*.py")):
        digest.update(source.read_bytes())
    return digest.hexdigest()[:8]


def config_version(settings) -> str:
    """A short hash of the answer-shaping settings and of the prompts (see _CONFIG_FIELDS)."""
    values = {name: str(getattr(settings, name, None)) for name in _CONFIG_FIELDS}
    payload = json.dumps(values, sort_keys=True) + prompt_version()
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def model_label(settings) -> str:
    """The answer model's name, for the trace row."""
    return str(getattr(settings, "LITELLM_MODEL_NAME", None) or getattr(settings, "LLM_TYPE", "") or "")[:100]


def _stored_name(metadata: dict) -> str:
    """The file name of a chunk indexed before file_name was recorded: the tail of its stored path
    ("<uuid>_<name>/texts/<chunk>")."""
    key = str(metadata.get("original_file_path") or "").split("/texts/")[0]
    return key.rsplit("/", 1)[-1]


def hit_summary(hit: dict) -> dict:
    """One retrieved passage as the trace records it: id, where it came from, how it scored."""
    metadata = hit.get("metadata") or {}
    return {
        "id": str(hit.get("id") or "")[:64],
        "file": (metadata.get("file_name") or _stored_name(metadata))[:200],
        "page": metadata.get("page_number"),
        "score": round(float(hit.get("score") or 0.0), 4),
    }


def hits_summary(hits: list, limit: int) -> list:
    """The first `limit` hits as the trace records them (Settings.TRACE_MAX_HITS): the trace explains a
    turn, it is not a copy of the index."""
    return [hit_summary(hit) for hit in hits[:limit]]


def top_score_of(hits: list) -> float | None:
    scores = [float(hit.get("score") or 0.0) for hit in hits]
    return max(scores) if scores else None


def trace_params(history_id: int, outcome: str, settings, total_ms: int | None, top_score: float | None, trace: dict) -> list:
    """upsert_chatbot_trace: [history_id, outcome, model, config_version, total_ms, top_score, trace json]."""
    return [
        history_id, outcome, model_label(settings), config_version(settings), total_ms, top_score,
        json.dumps({**trace, "prompt_version": prompt_version()}, ensure_ascii=False, default=str),
    ]


def outcome_of(intent: str | None, no_evidence: bool) -> str:
    """The trace outcome of a finished turn: a small-talk intent, the evidence gate, or a real answer."""
    if intent:
        return intent
    return OUTCOME_NO_EVIDENCE if no_evidence else OUTCOME_ANSWERED


def clipped(text: str, chars: int) -> str:
    """A search query cut to Settings.TRACE_QUERY_MAX_CHARS."""
    return text[:chars]
