"""The knowledge-base catalog as prompt text, and the rebuild-and-compare logic behind a refresh."""
import pytest
from app.logic.catalog import (
    catalog_entry,
    clip,
    description_from_reply,
    documents_changed,
    empty_catalog,
    existing_documents,
    indexed_rows,
    names_a_document,
    parse_catalog,
    render_catalog,
    reusable_description,
    serialize_catalog,
)

NAME_WIDTH = 60  # Settings.KB_CATALOG_NAME_WIDTH
MIN_CHARS = 3  # Settings.KB_NAME_MENTION_MIN_CHARS


def _docs(n, description="A description of what it covers."):
    return [{"file_id": i, "name": f"Document number {i}.pdf", "description": description} for i in range(1, n + 1)]


def test_clip_leaves_short_text_alone():
    assert clip("  Short.  ", 50) == "Short."


def test_clip_cuts_at_the_end_of_a_sentence_when_one_is_in_range():
    assert clip("One sentence here. Two sentences here. Three here ok.", 30) == "One sentence here."


def test_clip_otherwise_cuts_at_a_word_and_says_so():
    assert clip("word " * 30, 12) == "word word…"


def test_the_catalog_lists_each_document_with_its_description():
    text, names = render_catalog(_docs(2), 3500, NAME_WIDTH)
    assert text == (
        "Knowledge base (2 documents):\n"
        "- Document number 1: A description of what it covers.\n"
        "- Document number 2: A description of what it covers."
    )
    assert names == "Document number 1; Document number 2"


def test_a_document_with_no_description_is_listed_by_name_and_one_is_singular():
    text, _ = render_catalog([{"name": "Policy.docx", "description": ""}, ], 3500, NAME_WIDTH)
    assert text == "Knowledge base (1 document):\n- Policy"


def test_an_empty_knowledge_base_renders_nothing():
    assert render_catalog([], 3500, NAME_WIDTH) == ("", "")


def test_too_many_descriptions_are_cut_back_to_names_only_while_every_document_keeps_its_name():
    docs = _docs(20)
    full, _ = render_catalog(docs, 100_000, NAME_WIDTH)
    text, names = render_catalog(docs, 700, NAME_WIDTH)
    assert len(text) < len(full)
    lines = text.splitlines()[1:]
    assert len(lines) == 20
    assert any(":" in line for line in lines[:3]), "the first documents still get their descriptions"
    assert lines[-1] == "- Document number 20", "the last ones are names only"
    assert len(names.split("; ")) == 20


def test_a_reply_that_names_a_document_is_recognised_ignoring_case_and_ellipses():
    names = "SAMA Cyber Framework; UAE Information Assuran…"
    assert names_a_document("We hold the sama cyber framework.", names, MIN_CHARS)
    assert names_a_document("the uae information assuran is covered", names, MIN_CHARS)
    assert not names_a_document("I can answer questions from my documents.", names, MIN_CHARS)


def test_very_short_or_missing_names_never_match():
    assert not names_a_document("anything", "", MIN_CHARS)
    assert not names_a_document("ab", "ab", MIN_CHARS)


def test_a_name_shorter_than_the_minimum_is_ignored():
    assert names_a_document("ab is here", "ab", 2)
    assert not names_a_document("ab is here", "ab", 3)


def test_the_name_width_shortens_the_names():
    _, names = render_catalog([{"name": "A very long document name indeed.pdf", "description": ""}], 3500, 12)
    assert names == "A very long…"


def test_a_description_is_reused_only_while_the_name_is_unchanged():
    existing = {"7": {"file_id": 7, "name": "A.pdf", "description": "Kept."}}
    assert reusable_description(existing, {"id": 7, "name": "A.pdf"}) == "Kept."
    assert reusable_description(existing, {"id": 7, "name": "Renamed.pdf"}) is None
    assert reusable_description(existing, {"id": 8, "name": "A.pdf"}) is None
    assert reusable_description({"7": {"name": "A.pdf", "description": ""}}, {"id": 7, "name": "A.pdf"} ) == ""


def test_the_catalog_is_only_rewritten_when_an_id_name_or_description_changed():
    stored = {"1": {"file_id": 1, "name": "A", "description": "d"}}
    assert not documents_changed([{"file_id": 1, "name": "A", "description": "d"}], stored)
    assert documents_changed([{"file_id": 1, "name": "A", "description": "new"}], stored)
    assert documents_changed([{"file_id": 1, "name": "B", "description": "d"}], stored)
    assert documents_changed([], stored)
    assert documents_changed([{"file_id": 1, "name": "A", "description": "d"}], {})


# --- the stored file and its rebuild ---

def test_a_stored_catalog_round_trips_through_bytes():
    catalog = {"updated_at": "2026-01-01T00:00:00+00:00", "documents": [{"file_id": 1, "name": "Ünï.pdf", "description": "d"}]}
    assert parse_catalog(serialize_catalog(catalog)) == catalog


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b'{"documents": "x"}', b"{}", b"\xff\xfe"])
def test_a_stored_file_that_is_not_a_catalog_is_refused(raw):
    with pytest.raises(ValueError):
        parse_catalog(raw)


def test_an_empty_catalog_is_a_new_one_each_time():
    assert empty_catalog() == {"documents": []} and empty_catalog() is not empty_catalog()


def test_only_indexed_files_are_listed_by_name_ignoring_case():
    rows = [
        {"id": 1, "name": "b.pdf", "vector_store_file_id": "k1"},
        {"id": 2, "name": "A.pdf", "vector_store_file_id": "k2"},
        {"id": 3, "name": "pending.pdf", "vector_store_file_id": None},
    ]
    assert [r["id"] for r in indexed_rows([rows])] == [2, 1]
    assert indexed_rows([]) == []


def test_stored_documents_are_keyed_by_file_id_as_text():
    assert existing_documents({"documents": [{"file_id": 7, "name": "A"}]}) == {"7": {"file_id": 7, "name": "A"}}
    assert existing_documents({}) == {}


def test_a_catalog_entry_leaves_a_missing_description_empty():
    assert catalog_entry({"id": 7, "name": "A.pdf", "extra": 1}, None) == {"file_id": 7, "name": "A.pdf", "description": ""}
    assert catalog_entry({"id": 7, "name": "A.pdf"}, "d")["description"] == "d"


@pytest.mark.parametrize("reply,expected", [
    ('{"description": "  A  regulation   on cyber. "}', "A regulation on cyber."),
    ('```json\n{"description": "Fenced."}\n```', "Fenced."),
    ('{"description": "   "}', None),
    ('{"description": 5}', None),
    ('{"other": "x"}', None),
    ("not json", None),
    (None, None),
])
def test_a_describe_reply_yields_a_tidy_description_or_nothing(reply, expected):
    assert description_from_reply(reply) == expected
