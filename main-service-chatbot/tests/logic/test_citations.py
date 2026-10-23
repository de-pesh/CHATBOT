"""Citation markers: rendering them as links, and stripping them back off."""
from app.logic.citations import (
    cited_url_keys,
    render_markers,
    short_name,
    strip_citations,
)

SOURCES = {
    1: {"n": 1, "file_name": "SAMA Cyber Framework v1.pdf", "page": 5},
    2: {"n": 2, "file_name": "Policy.docx", "page": None},
    3: {"n": 3, "file_name": "a' onmouseover='alert(1).pdf", "page": 2},
}
URLS = {
    "SAMA Cyber Framework v1.pdf": "https://files/sama?sig=1&x=2",
    "Policy.docx": None,
    "a' onmouseover='alert(1).pdf": "https://files/evil",
}
STYLE = "color:#eed202"  # Settings.CITATION_LINK_STYLE
NO_URLS = {source["file_name"]: None for source in SOURCES.values()}


def test_short_name_drops_the_extension_and_tidies_spacing():
    assert short_name("UAE_Information  Assurance Regulation v1 1 pdf.pdf", 32) == "UAE Information Assurance…"
    assert short_name("report.PDF", 32) == "report"


def test_short_name_cuts_a_single_very_long_word_where_it_stands():
    assert short_name("x" * 80 + ".docx", 18) == "x" * 17 + "…"


def test_cited_url_keys_are_in_order_of_first_use_and_skip_made_up_numbers():
    assert cited_url_keys("A [3]. B [1, 9]. C [3] [2]", SOURCES) == [
        "a' onmouseover='alert(1).pdf", "SAMA Cyber Framework v1.pdf", "Policy.docx",
    ]
    assert cited_url_keys("no markers", SOURCES) == []


def test_a_cited_marker_becomes_a_linked_label_with_the_page():
    out = render_markers("Banks are supervised [1].", SOURCES, URLS, STYLE, 18)
    assert out.startswith("Banks are supervised <a target='_blank'")
    assert "href='https://files/sama?sig=1&amp;x=2'" in out
    assert ", Pg: 5]</a>." in out
    assert "title='SAMA Cyber Framework v1.pdf, Page: 5'" in out


def test_the_link_style_and_label_width_are_the_callers():
    out = render_markers("x [1]", SOURCES, URLS, "color:red", 8)
    assert "style='color:red'" in out
    assert "[SAMA…, Pg: 5]" in out


def test_a_document_with_no_url_is_left_as_a_plain_label():
    assert render_markers("See [2].", SOURCES, URLS, STYLE, 18) == "See [Policy]."


def test_a_file_name_cannot_break_out_of_the_anchor_attributes():
    out = render_markers("x [3]", SOURCES, URLS, STYLE, 18)
    assert "onmouseover='alert" not in out
    assert "&#x27;" in out


def test_a_made_up_marker_vanishes_with_the_space_before_it():
    assert render_markers("Claim [9].", SOURCES, URLS, STYLE, 18) == "Claim."


def test_one_document_cited_twice_in_a_marker_is_shown_once():
    out = render_markers("x [1, 1]", {1: SOURCES[1]}, {SOURCES[1]["file_name"]: None}, STYLE, 18)
    assert out.count("[SAMA") == 1


def test_strip_citations_round_trips_linked_and_plain_answers_back_to_prose():
    prose = "Banks are supervised. Insurers too."
    text = "Banks are supervised [1]. Insurers too [3]."
    assert strip_citations(render_markers(text, SOURCES, URLS, STYLE, 18)) == prose
    assert strip_citations(render_markers(text, SOURCES, NO_URLS, STYLE, 18)) == prose


def test_strip_citations_removes_raw_markers_and_the_sources_list_and_keeps_other_links():
    assert strip_citations("Answer [1][2].\n\nSources:\n[1] a.pdf\n[2] b.pdf") == "Answer."
    assert strip_citations("See <a target='_blank' href='https://x'>the docs</a> [4]") == "See the docs"


def test_strip_citations_passes_empty_text_through():
    assert strip_citations("") == ""
    assert strip_citations(None) is None


# ---- two versions of one document

V2 = {"n": 1, "file_name": "Policy.pdf", "stored_path": "u/2_Policy.pdf", "page": 4, "version": "v2", "older": True}
V3 = {"n": 2, "file_name": "Policy.pdf", "stored_path": "u/3_Policy.pdf", "page": 4, "version": "v3", "older": False}


def test_two_versions_that_share_a_name_get_their_own_link_and_a_version_tag():
    from app.logic.citations import render_markers, url_key

    assert url_key(V2) != url_key(V3) and url_key({"file_name": "a.pdf"}) == "a.pdf"
    urls = {url_key(V2): "https://x/2", url_key(V3): "https://x/3"}
    out = render_markers("Was 90 days [1], now 60 [2].", {1: V2, 2: V3}, urls, "", 40)
    assert "href='https://x/2'" in out and "href='https://x/3'" in out
    assert "[Policy v2, Pg: 4]" in out and "[Policy v3, Pg: 4]" in out
    assert "Policy.pdf (v2), Page: 4" in out  # the hover text names the version too


def test_a_source_with_no_version_is_labelled_as_before():
    from app.logic.citations import render_markers

    plain = {"n": 1, "file_name": "Policy.pdf", "stored_path": "", "page": 4, "version": "", "older": False}
    assert render_markers("x [1]", {1: plain}, {"Policy.pdf": None}, "", 40) == "x [Policy, Pg: 4]"
