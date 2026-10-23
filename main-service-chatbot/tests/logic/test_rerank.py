"""Dedupe before the rerank call, and mapping its reply back onto the candidates."""
from app.logic.rerank import (
    dedupe_candidates,
    fuse_query_hits,
    reorder_by_ids,
    rerank_short_id,
)


def _hit(i, score=0.5):
    return {"id": f"{i:08d}-aaaa-bbbb-cccc-dddddddddddd", "score": score, "metadata": {"textContent": f"t{i}"}}


def test_dedupe_keeps_the_best_scoring_occurrence_of_a_chunk():
    low, high = _hit(1, 0.2), _hit(1, 0.9)
    assert dedupe_candidates([low, _hit(2), high]) == [high, _hit(2)]


def test_dedupe_falls_back_to_the_text_for_hits_with_no_id_and_drops_keyless_ones():
    a = {"metadata": {"textContent": "same"}, "score": 0.1}
    b = {"metadata": {"textContent": "same"}, "score": 0.3}
    assert dedupe_candidates([a, b, {"score": 0.9}]) == [b]


def test_short_id_is_the_start_of_the_id():
    assert rerank_short_id("12345678-aaaa", 8) == "12345678"
    assert rerank_short_id(None, 8) == ""


def test_reorder_follows_the_models_order_and_reads_short_or_full_ids():
    cands = [_hit(1), _hit(2), _hit(3)]
    assert reorder_by_ids(cands, ["00000003", cands[0]["id"]], 8, top_k=5) == [cands[2], cands[0]]


def test_reorder_skips_invented_ids_and_a_repeat_cannot_take_a_second_slot():
    cands = [_hit(1), _hit(2)]
    assert reorder_by_ids(cands, ["zzz", "00000002", " 00000002 ", "00000001"], 8, top_k=5) == [cands[1], cands[0]]


def test_reorder_keeps_at_most_top_k():
    cands = [_hit(i) for i in range(1, 6)]
    assert reorder_by_ids(cands, [f"{i:08d}" for i in range(1, 6)], 8, top_k=2) == cands[:2]


def test_reorder_is_empty_when_nothing_matched_so_the_caller_can_fall_back():
    assert reorder_by_ids([_hit(1)], ["nomatch"], 8, top_k=3) == []
    assert reorder_by_ids([{"score": 1.0}], ["x"], 8, top_k=3) == []


# ------------------------------------------------------------------ fusing several searches

def _ids(hits):
    return [int(h["id"][:8]) for h in hits]


def test_a_chunk_two_searches_agree_on_beats_one_only_one_search_ranked_first():
    original = [_hit(1, 0.09), _hit(2, 0.08), _hit(3, 0.07)]
    alternate = [_hit(4, 0.09), _hit(2, 0.08), _hit(5, 0.07)]
    fused = fuse_query_hits([original, alternate], k=10)
    assert _ids(fused)[0] == 2, "second in both lists outranks first in only one"
    assert set(_ids(fused)) == {1, 2, 3, 4, 5}, "every chunk survives, once"


def test_the_fused_score_is_the_sum_of_reciprocal_ranks():
    fused = fuse_query_hits([[_hit(1, 0.9), _hit(2, 0.1)], [_hit(2, 0.9)]], k=10)
    by_id = {h["id"][:8]: h["score"] for h in fused}
    assert by_id["00000001"] == 1 / 11
    assert by_id["00000002"] == 1 / 12 + 1 / 11


def test_ranks_come_from_each_searchs_own_scores_not_the_order_it_arrived_in():
    fused = fuse_query_hits([[_hit(1, 0.1), _hit(2, 0.9)], [_hit(3, 0.5)]], k=10)
    assert _ids(fused) == [2, 3, 1]


def test_one_search_is_returned_untouched_with_its_own_scores():
    only = [_hit(1, 0.09), _hit(2, 0.08)]
    assert fuse_query_hits([only], k=10) == only
    assert fuse_query_hits([only, []], k=10) == only, "an empty search is not a second opinion"
    assert fuse_query_hits([], k=10) == [] and fuse_query_hits([[], []], k=10) == []


def test_a_chunk_repeated_inside_one_search_counts_once_and_keyless_hits_are_dropped():
    fused = fuse_query_hits([[_hit(1, 0.9), _hit(1, 0.8), {"score": 0.7}], [_hit(2, 0.5)]], k=10)
    assert _ids(fused) == [1, 2]
    assert fused[0]["score"] == 1 / 11


def test_fusing_does_not_change_the_hits_it_was_given():
    a, b = _hit(1, 0.9), _hit(1, 0.4)
    fuse_query_hits([[a], [b]], k=10)
    assert a["score"] == 0.9 and b["score"] == 0.4
