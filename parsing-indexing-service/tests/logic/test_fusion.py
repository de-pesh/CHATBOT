import numpy as np
from app.logic.fusion import cosine_top_k, reciprocal_rank_fusion, to_hit, usable_docs


def hit(doc_id, score=0.0):
    return {"id": doc_id, "textContent": f"text {doc_id}", "metadata": {}, "score": score}


def test_a_chunk_both_lists_agree_on_beats_one_only_the_vector_list_likes():
    vector = [hit("a"), hit("b"), hit("c")]
    lexical = [hit("c"), hit("b")]
    fused = reciprocal_rank_fusion(vector, lexical, limit=3, k=10, lexical_weight=0.5)
    assert [h["id"] for h in fused] == ["b", "c", "a"]


def test_scores_are_the_weighted_reciprocal_ranks():
    fused = reciprocal_rank_fusion([hit("a")], [hit("a")], limit=1, k=10, lexical_weight=0.5)
    assert fused[0]["score"] == 1 / 11 + 0.5 / 11


def test_a_higher_lexical_weight_can_reorder_the_result():
    vector, lexical = [hit("v1")], [hit("l1")]
    light = reciprocal_rank_fusion(vector, lexical, 2, k=10, lexical_weight=0.5)
    heavy = reciprocal_rank_fusion(vector, lexical, 2, k=10, lexical_weight=2.0)
    assert [h["id"] for h in light] == ["v1", "l1"]
    assert [h["id"] for h in heavy] == ["l1", "v1"]


def test_limit_and_the_input_hits_are_left_alone():
    vector = [hit(str(i), score=0.9) for i in range(5)]
    fused = reciprocal_rank_fusion(vector, [], limit=2, k=10, lexical_weight=0.5)
    assert len(fused) == 2
    assert vector[0]["score"] == 0.9  # the caller's dict was copied, not rewritten


def test_hits_without_an_id_are_told_apart_by_their_text():
    fused = reciprocal_rank_fusion([{"textContent": "x"}, {"textContent": "y"}], [], 5, k=10, lexical_weight=0.5)
    assert len(fused) == 2


def test_cosine_top_k_orders_by_similarity():
    matrix = np.array([[1, 0], [0, 1], [1, 1]], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    top = cosine_top_k(matrix, norms, [1, 0.1], limit=2)
    assert [i for i, _ in top] == [0, 2]
    assert top[0][1] > top[1][1]
    assert abs(cosine_top_k(matrix, norms, [2, 0], 1)[0][1] - 1.0) < 1e-6


def test_cosine_top_k_scores_a_zero_vector_as_zero_instead_of_nan():
    matrix = np.array([[0, 0], [1, 0]], dtype=np.float32)
    top = cosine_top_k(matrix, np.linalg.norm(matrix, axis=1), [1, 0], limit=2)
    assert dict(top)[0] == 0.0
    assert top[0][0] == 1


def test_to_hit_copies_the_metadata_and_reads_the_uuid():
    doc = {"textContent": "hello", "metadata": {"uuid": "u1", "page_number": 3}}
    result = to_hit(doc, 0.5)
    assert result == {"id": "u1", "textContent": "hello", "metadata": {"uuid": "u1", "page_number": 3}, "score": 0.5}
    result["metadata"]["textContent"] = "added by the caller"
    assert "textContent" not in doc["metadata"]


def test_to_hit_tolerates_a_chunk_with_no_metadata():
    assert to_hit({"textContent": "t"}, 1.0)["id"] is None


def test_usable_docs_drops_wrong_length_and_non_list_vectors():
    docs = [{"v": [1, 2, 3]}, {"v": [1, 2]}, {"v": "nope"}, {}]
    assert usable_docs(docs, "v", 3) == [{"v": [1, 2, 3]}]


def test_each_list_is_fetched_at_least_the_minimum_depth_and_scales_with_the_limit():
    from app.logic.fusion import fusion_depth
    assert fusion_depth(3, 4, 40) == 40
    assert fusion_depth(20, 4, 40) == 80


def test_a_hit_becomes_rpc_fields_with_the_text_inside_the_metadata_and_is_not_mutated():
    from app.logic.fusion import result_fields
    hit = {"id": "u", "score": 0.5, "textContent": "body", "metadata": {"page_number": 2}}
    assert result_fields(hit) == ("u", 0.5, {"page_number": 2, "textContent": "body"})
    assert hit["metadata"] == {"page_number": 2}
    assert result_fields({}) == ("", 0.0, {"textContent": ""})
