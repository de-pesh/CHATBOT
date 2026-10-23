"""Upload rules, stored names and file identifiers."""
import pytest
from app.logic.files import (
    azure_blob_name,
    classify_file_id,
    download_name,
    extract_blob_path_from_url,
    is_supported_extension,
    max_upload_bytes,
    stored_name,
)

ALLOWED = ".pdf, .DOCX,.txt"
UUID = "0b3f2a5c-1d4e-4f60-8a7b-9c0d1e2f3a4b"


@pytest.mark.parametrize("name,expected", [
    ("report.pdf", True), ("REPORT.PDF", True), ("a.b.docx", True), ("notes.txt", True),
    ("image.png", False), ("noextension", False), (".pdf", True), ("archive.pdf.exe", False),
])
def test_an_upload_is_accepted_by_its_last_extension_ignoring_case(name, expected):
    assert is_supported_extension(name, ALLOWED) is expected


def test_the_size_limit_is_in_bytes():
    assert max_upload_bytes(50) == 50 * 1024 * 1024


def test_a_stored_name_keeps_the_original_and_the_download_name_recovers_it():
    stored = stored_name(UUID, "Policy v1.pdf")
    assert stored == f"{UUID}_Policy v1.pdf"
    assert download_name(stored) == "Policy v1.pdf"
    assert download_name(f"folder/sub/{stored}") == "Policy v1.pdf"
    assert download_name("plain.pdf") == "plain.pdf"


@pytest.mark.parametrize("reference,expected", [
    ("https://acct.blob.core.windows.net/container/abc_report.pdf", "abc_report.pdf"),
    ("https://acct.blob.core.windows.net/container/dir/abc.pdf", "dir/abc.pdf"),
    ("https://host/only", "only"),
    ("abc_report.pdf", "abc_report.pdf"),
    ("/abc_report.pdf", "abc_report.pdf"),
    ("invoice:2024.pdf", "invoice:2024.pdf"),
    ("", ""),
])
def test_a_stored_reference_is_reduced_to_a_store_path(reference, expected):
    assert extract_blob_path_from_url(reference) == expected


def test_an_azure_reference_loses_only_its_own_container_prefix():
    assert azure_blob_name("https://acct.blob.core.windows.net/box/abc.pdf", "box") == "abc.pdf"
    assert azure_blob_name("https://acct.blob.core.windows.net/other/abc.pdf", "box") == "other/abc.pdf"
    assert azure_blob_name("/abc.pdf", "box") == "abc.pdf"
    assert azure_blob_name("abc.pdf", "box") == "abc.pdf"
    once = extract_blob_path_from_url("https://acct.blob.core.windows.net/box/abc.pdf")
    assert azure_blob_name(once, "box") == once, "safe to apply after the generic reduction"


def test_a_file_id_is_a_record_id_or_a_knowledge_base_uuid():
    assert classify_file_id("42") == ("sql", 42)
    assert classify_file_id(UUID) == ("kb", UUID)


@pytest.mark.parametrize("bad", ["abc", "", "12.5", "not-a-uuid-at-all"])
def test_anything_else_is_not_an_identifier(bad):
    with pytest.raises(ValueError):
        classify_file_id(bad)


def test_upload_problem_rejects_empty_and_mismatched_content():
    from app.logic.files import upload_problem

    assert upload_problem("a.pdf", b"") == "a.pdf is empty."
    assert upload_problem("a.pdf", b"not a pdf") is not None
    assert upload_problem("a.docx", b"%PDF-1.7") is not None


def test_upload_problem_accepts_matching_and_signatureless_content():
    from app.logic.files import upload_problem

    assert upload_problem("a.pdf", b"%PDF-1.7 ...") is None
    assert upload_problem("a.docx", b"PK\x03\x04rest") is None
    assert upload_problem("notes.txt", b"anything") is None
