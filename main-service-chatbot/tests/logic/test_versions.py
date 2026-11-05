"""Which uploads are versions of one document, and which versions a question wants."""
import pytest
from app.logic.versions import (
    LATEST,
    Mode,
    VersionScope,
    detect_scope,
    is_older,
    split_document_name,
    version_tag,
)


@pytest.mark.parametrize("name, expected", [
    ("Policy.pdf", ("policy", None)),
    ("  Information  Security Policy.PDF ", ("information security policy", None)),
    ("Policy_v3.2.pdf", ("policy", "3.2")),
    ("Policy v2.docx", ("policy", "2")),
    ("Policy-V10.pdf", ("policy", "10")),
    ("Policy.docx", ("policy", None)),           # a new format is the next version of the same document
    ("report_final.pdf", ("report_final", None)),
    ("v2.pdf", ("v2", None)),
    # Bare year suffix — links "policy_2024.pdf" and "policy_2025.pdf" as the same document
    ("policy_2024.pdf", ("policy", "2024")),
    ("policy 2025.docx", ("policy", "2025")),
    ("Procurement Policy-2023.pdf", ("procurement policy", "2023")),
    ("report1990.pdf", ("report1990", None)),     # no separator → not a version tag
])
def test_a_file_name_becomes_a_document_key_and_an_optional_version_label(name, expected):
    assert split_document_name(name) == expected


def test_the_same_document_in_two_formats_and_versions_shares_a_key():
    assert split_document_name("Policy_v1.pdf")[0] == split_document_name("policy V2.docx")[0]


def test_year_suffixed_files_share_a_key():
    assert split_document_name("policy_2024.pdf")[0] == split_document_name("policy_2025.pdf")[0] == "policy"


@pytest.mark.parametrize("question, mode, token", [
    ("What is the encryption requirement?", Mode.LATEST, None),
    ("What is the current version of the policy?", Mode.LATEST, None),
    ("Compare the current policy with the previous version", Mode.ALL, None),
    ("What changed in the password rules?", Mode.ALL, None),
    ("what are the differences between v2 and v3", Mode.ALL, None),
    ("Between version 2 and version 3, how did retention change?", Mode.ALL, None),
    ("What did the previous version say about retention?", Mode.PREVIOUS, None),
    ("It used to say something else, what was it?", Mode.PREVIOUS, None),
    ("What does version 2 require?", Mode.SPECIFIC, "2"),
    ("what does v3.2 say about backups", Mode.SPECIFIC, "3.2"),
    # User-reported phrases
    ("what is this as per v4 document", Mode.SPECIFIC, "4"),
    ("as per v4", Mode.SPECIFIC, "4"),
    ("whats difference in v4 & v5", Mode.ALL, None),
    ("what changed between v4 and v5", Mode.ALL, None),
    # Bare year references
    ("what is abc according to policy 2024", Mode.SPECIFIC, "2024"),
    ("what does the 2024 policy say about X", Mode.SPECIFIC, "2024"),
    ("compare 2024 and 2025 versions", Mode.ALL, None),
    # Year as time reference — must NOT trigger SPECIFIC
    ("what happened in 2024", Mode.LATEST, None),
    ("what changed since 2023", Mode.ALL, None),   # "what changed" triggers compare regardless of year
    ("requirements before 2025", Mode.LATEST, None),
])
def test_the_wording_of_a_question_picks_the_versions(question, mode, token):
    assert detect_scope(question) == VersionScope(mode, token)


def test_an_empty_question_uses_the_latest():
    assert detect_scope("") == LATEST and detect_scope(None) == LATEST


LATEST_CHUNK = {"version_no": 3, "is_latest": True}
OLD_CHUNK = {"version_no": 2, "version_label": "2.1", "is_latest": False}
LEGACY_CHUNK = {"file_name": "old.pdf"}  # indexed before versions existed


def test_the_default_scope_keeps_only_the_latest_and_treats_old_chunks_as_latest():
    assert LATEST.matches(LATEST_CHUNK) and LATEST.matches(LEGACY_CHUNK)
    assert not LATEST.matches(OLD_CHUNK)


def test_previous_keeps_only_the_replaced_versions_and_all_keeps_everything():
    previous, everything = VersionScope(Mode.PREVIOUS), VersionScope(Mode.ALL)
    assert previous.matches(OLD_CHUNK) and not previous.matches(LATEST_CHUNK) and not previous.matches(LEGACY_CHUNK)
    assert all(everything.matches(c) for c in (LATEST_CHUNK, OLD_CHUNK, LEGACY_CHUNK))


def test_a_specific_scope_matches_the_number_or_the_label_and_a_legacy_chunk_is_version_one():
    assert VersionScope(Mode.SPECIFIC, "2").matches(OLD_CHUNK)
    assert VersionScope(Mode.SPECIFIC, "2.1").matches(OLD_CHUNK)
    assert not VersionScope(Mode.SPECIFIC, "3").matches(OLD_CHUNK)
    assert VersionScope(Mode.SPECIFIC, "1").matches(LEGACY_CHUNK)


def test_tags_and_older_flag():
    assert version_tag(OLD_CHUNK) == "v2.1" and version_tag(LATEST_CHUNK) == "v3" and version_tag(LEGACY_CHUNK) == ""
    assert is_older(OLD_CHUNK) and not is_older(LATEST_CHUNK) and not is_older(LEGACY_CHUNK)


@pytest.mark.parametrize("label, expected", [("3.2", "3.2"), (" draft-1 ", "draft-1"), ("2026_Q1", "2026_Q1"), ("", None), (None, None), ("   ", None)])
def test_a_plain_version_label_is_kept_and_an_empty_one_is_none(label, expected):
    from app.logic.versions import clean_label

    assert clean_label(label) == expected


@pytest.mark.parametrize("label", ["</context>", "<b>", "a b", "v1\nSYSTEM", "-start", "x" * 51, "a;b", "é"])
def test_a_label_with_anything_a_prompt_or_page_should_not_get_is_refused(label):
    from app.logic.versions import clean_label

    with pytest.raises(ValueError):
        clean_label(label)
