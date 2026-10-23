import pytest
from app.logic.chunking import (
    _extract_control_ids,
    _is_docling_json,
    chunk_records,
    split_document,
    stamp_metadata,
)
from app.logic.text_formats import PAGE_BREAK


# ---------------------------------------------------------------------------
# DoclingDocument JSON detection
# ---------------------------------------------------------------------------

def test_is_docling_json_recognises_model_dump_json_output():
    valid = '{"schema_name":"DoclingDocument","version":"1.3.0","name":"test.pdf"}'
    assert _is_docling_json(valid)


def test_is_docling_json_rejects_plain_text_and_markdown():
    assert not _is_docling_json("plain text")
    assert not _is_docling_json("# Heading\nbody text")
    assert not _is_docling_json('{"schema_name":"SomethingElse"}')


def test_docling_json_blob_routes_to_docling_chunker_not_character_splitter(monkeypatch):
    from app.logic import chunking
    fake_json = '{"schema_name":"DoclingDocument","version":"1.3.0","name":"test"}'
    sentinel = [{"text": "AC-1 chunk", "metadata": {"section_heading": "# Access Control", "control_ids": ["AC-1"]}}]
    monkeypatch.setattr(chunking, "_split_docling_document", lambda _: sentinel)
    assert split_document(fake_json, 100, 10, by_page=False) == sentinel
    assert split_document(fake_json, 100, 10, by_page=True) == sentinel


# ---------------------------------------------------------------------------
# GRC control ID extraction
# ---------------------------------------------------------------------------

def test_extract_control_ids_finds_nist_iso_soc2_patterns():
    text = "Controls AC-1 and AC-2.1 map to A.9.1.1 and CC6.1 per the assessment."
    ids = _extract_control_ids(text)
    assert "AC-1" in ids
    assert "AC-2.1" in ids
    assert "A.9.1.1" in ids
    assert "CC6.1" in ids


def test_extract_control_ids_does_not_match_plain_version_numbers():
    # "1.2" alone is a version number; "CC" prefix distinguishes SOC 2 controls.
    assert "1.2" not in _extract_control_ids("See version 1.2 of the document.")
    assert "1.0" not in _extract_control_ids("Updated in release 1.0.")


def test_extract_control_ids_is_deduplicated_and_order_preserving():
    text = "AC-1 requires that AC-1 is documented and reviewed; see also AC-2."
    ids = _extract_control_ids(text)
    assert ids.count("AC-1") == 1
    assert ids.index("AC-1") < ids.index("AC-2")


def test_extract_control_ids_returns_empty_for_text_with_no_controls():
    assert _extract_control_ids("This document has no control references.") == []


def test_plain_chunks_carry_no_page_number():
    chunks = split_document("one two three", 100, 10, by_page=False)
    assert chunks == [{"text": "one two three", "metadata": {}}]


def test_by_page_numbers_each_chunk_with_its_page():
    text = PAGE_BREAK.join(["first page", "second page"])
    chunks = split_document(text, 100, 10, by_page=True)
    assert chunks == [
        {"text": "first page", "metadata": {"page_number": 1}},
        {"text": "second page", "metadata": {"page_number": 2}},
    ]


def test_blank_pages_are_skipped_but_still_counted():
    text = PAGE_BREAK.join(["first", "  \n ", "third"])
    assert [c["metadata"]["page_number"] for c in split_document(text, 100, 10, by_page=True)] == [1, 3]


def test_a_chunk_never_spans_two_pages():
    text = PAGE_BREAK.join(["a " * 30, "b " * 30])
    chunks = split_document(text, 40, 5, by_page=True)
    assert len(chunks) > 2
    for chunk in chunks:
        assert set(chunk["text"].replace(" ", "")) in ({"a"}, {"b"})


def test_a_page_break_is_ordinary_text_when_not_paging():
    chunks = split_document(PAGE_BREAK.join(["first", "second"]), 1000, 10, by_page=False)
    assert len(chunks) == 1 and "first" in chunks[0]["text"] and "second" in chunks[0]["text"]


def test_long_text_is_split_within_the_size_and_overlaps():
    text = " ".join(f"word{i}" for i in range(200))
    chunks = split_document(text, 100, 30, by_page=False)
    assert len(chunks) > 1
    assert all(len(c["text"]) <= 100 for c in chunks)
    first_tail = chunks[0]["text"].split()[-1]
    assert first_tail in chunks[1]["text"].split()  # the overlap repeats the boundary words


def test_whitespace_only_text_gives_no_chunks():
    assert split_document("   \n ", 100, 10, by_page=False) == []
    assert split_document(PAGE_BREAK, 100, 10, by_page=True) == []


def test_an_overlap_that_is_negative_or_not_smaller_than_the_size_is_refused():
    for size, overlap in ((100, 100), (100, 150), (100, -1), (0, 0)):
        with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
            split_document("text", size, overlap, by_page=False)


def test_chunk_records_pair_each_chunk_with_its_vector_and_the_model_that_made_it():
    import numpy as np
    chunks = [{"text": "a", "metadata": {"page_number": 1}}, {"text": "b", "metadata": {}}]
    records = chunk_records(chunks, np.array([[1.0, 2.0], [3.0, 4.0]]), "m")
    assert records == [
        {"textContent": "a", "vectorContent": [1.0, 2.0], "embeddingModel": "m", "metadata": {"page_number": 1}},
        {"textContent": "b", "vectorContent": [3.0, 4.0], "embeddingModel": "m", "metadata": {}},
    ]


def test_chunk_records_refuse_a_vector_matrix_of_the_wrong_shape():
    import numpy as np
    import pytest
    with pytest.raises(ValueError, match="shape mismatch"):
        chunk_records([{"text": "a", "metadata": {}}], np.zeros((2, 3)), "m")
    with pytest.raises(ValueError, match="shape mismatch"):
        chunk_records([{"text": "a", "metadata": {}}], np.zeros(3), "m")


# ---------------------------------------------------------------------------
# Heading-based splitting (Docling markdown)
# ---------------------------------------------------------------------------

def test_markdown_splits_at_each_atx_heading_and_keeps_heading_in_chunk():
    text = "# Section One\nContent one.\n## Sub\nContent two.\n# Section Two\nContent three."
    chunks = split_document(text, 1000, 0, by_page=False)
    texts = [c["text"] for c in chunks]
    assert texts[0] == "# Section One\nContent one."
    assert texts[1] == "## Sub\nContent two."
    assert texts[2] == "# Section Two\nContent three."
    assert all(c["metadata"]["section_heading"] != "" for c in chunks)


def test_markdown_heading_splitting_is_used_even_when_by_page_is_true():
    # Docling never writes PAGE_BREAK; by_page=True must not silently bypass heading splitting.
    text = "# Policy Section\nControl text here.\n## Sub-control\nDetails."
    chunks = split_document(text, 1000, 0, by_page=True)
    assert len(chunks) == 2
    assert chunks[0]["metadata"]["section_heading"] == "# Policy Section"
    assert chunks[1]["metadata"]["section_heading"] == "## Sub-control"


def test_consecutive_headings_with_no_body_between_them_are_not_dropped():
    # Bug guard: "# H1\n## H2\ncontent" must produce a chunk for # H1, not silently drop it.
    text = "# Introduction\n## Background\nSome background text."
    chunks = split_document(text, 1000, 0, by_page=False)
    headings = [c["metadata"]["section_heading"] for c in chunks]
    assert "# Introduction" in headings
    assert "## Background" in headings


def test_preamble_text_before_first_heading_is_kept_as_its_own_chunk():
    text = "Preamble with no heading.\n# Section\nBody."
    chunks = split_document(text, 1000, 0, by_page=False)
    assert chunks[0]["text"] == "Preamble with no heading."
    assert chunks[0]["metadata"]["section_heading"] == ""
    assert chunks[1]["metadata"]["section_heading"] == "# Section"


def test_a_large_section_is_further_split_and_all_sub_chunks_carry_the_heading():
    long_body = "word " * 300  # ~1500 chars
    text = f"# Big Section\n{long_body}"
    chunks = split_document(text, 200, 20, by_page=False)
    assert len(chunks) > 1
    assert all(c["metadata"]["section_heading"] == "# Big Section" for c in chunks)
    assert all(len(c["text"]) <= 200 for c in chunks)


def test_markdown_with_no_headings_falls_back_to_character_splitting():
    text = "Just a paragraph with no headings at all. " * 10
    chunks = split_document(text, 100, 10, by_page=False)
    assert len(chunks) > 1
    assert all("section_heading" not in c["metadata"] for c in chunks)


def test_only_heading_no_body_produces_one_chunk_with_just_the_heading():
    chunks = split_document("# Title Only", 1000, 0, by_page=False)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "# Title Only"


def test_heading_inside_table_cell_is_not_treated_as_a_section_split():
    # "| # item |" starts with | not # so it must not match _HEADING_RE.
    text = "# Real Heading\n| # col | value |\n|---|---|\n| data | 1 |"
    chunks = split_document(text, 1000, 0, by_page=False)
    assert len(chunks) == 1  # table stays in the section body, not split
    assert "# col" in chunks[0]["text"]


# ---------------------------------------------------------------------------

def test_stamped_metadata_puts_the_callers_over_the_chunks_and_the_identifiers_over_both():
    stamped = stamp_metadata(
        {"page_number": 2, "isactive": "chunk"}, {"file_name": "f.pdf", "isactive": "caller"},
        uuid="u", source_file_id="s", original_file_path="p",
    )
    assert stamped == {"page_number": 2, "file_name": "f.pdf", "uuid": "u", "source_file_id": "s",
                       "original_file_path": "p", "isactive": True}
