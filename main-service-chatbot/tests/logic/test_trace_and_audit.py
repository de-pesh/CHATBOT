"""The per-turn trace and the admin audit rows: what gets recorded, and that it stays inside the columns."""
import json
from types import SimpleNamespace

from app.logic.audit import MAX_TARGET_CHARS, audit_params
from app.logic.trace import (
    OUTCOME_ANSWERED,
    OUTCOME_NO_EVIDENCE,
    config_version,
    hit_summary,
    hits_summary,
    outcome_of,
    top_score_of,
    trace_params,
)


def _settings(**overrides):
    values = {"LLM_TYPE": "azure", "RERANK_TOP_K": 5, "MIN_RELEVANCE_SCORE": 0.0}
    return SimpleNamespace(**{**values, **overrides})


def test_outcome_prefers_the_intent_then_the_gate_then_answered():
    assert outcome_of("chitchat", False) == "chitchat"
    assert outcome_of(None, True) == OUTCOME_NO_EVIDENCE
    assert outcome_of(None, False) == OUTCOME_ANSWERED


def test_config_version_changes_with_the_answer_shaping_settings_only():
    base = config_version(_settings())
    assert base == config_version(_settings())
    assert base != config_version(_settings(RERANK_TOP_K=8))
    assert base == config_version(_settings(SOMETHING_UNRELATED="x"))


def test_hit_summary_reads_the_metadata_the_index_writes():
    hit = {"id": "abc", "score": 0.123456, "metadata": {"file_name": "Policy.pdf", "page_number": 4.0}}
    assert hit_summary(hit) == {"id": "abc", "file": "Policy.pdf", "page": 4, "score": 0.1235}
    old = {"id": "z", "metadata": {"original_file_path": "dir/uuid_Old.pdf/texts/c1.txt"}}
    assert hit_summary(old)["file"] == "uuid_Old.pdf"
    assert hit_summary({}) == {"id": "", "file": "", "page": None, "score": 0.0}


def test_hits_are_capped_and_top_score_is_the_best():
    hits = [{"id": str(i), "score": i / 10} for i in range(100)]
    assert len(hits_summary(hits, 30)) == 30 and len(hits_summary(hits, 5)) == 5
    assert top_score_of(hits) == 9.9
    assert top_score_of([]) is None


def test_trace_params_are_positional_and_json_serialisable():
    params = trace_params(7, "answered", _settings(), 1234, 0.5, {"queries": ["q"]})
    assert params[:2] == [7, "answered"] and params[4:6] == [1234, 0.5]
    assert json.loads(params[6])["queries"] == ["q"] and "prompt_version" in json.loads(params[6])


def test_audit_params_cut_the_target_and_encode_the_detail():
    params = audit_params("a@x.com", "file_upload", "x" * 900, "ok", {"bytes": 3})
    assert params[0:2] == ["a@x.com", "file_upload"] and len(params[2]) == MAX_TARGET_CHARS
    assert json.loads(params[4]) == {"bytes": 3}
    assert audit_params("a", "b", None, "ok", None)[2:] == [None, "ok", None]
