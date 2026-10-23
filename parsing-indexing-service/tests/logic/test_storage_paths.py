import pytest
from app.logic.storage_paths import (
    blob_name,
    file_extension,
    parse_dir_mode,
    text_blob_path,
    volume_relative,
)


def test_the_extension_is_lowercased_and_taken_from_the_last_dot_of_the_file_name_only():
    assert file_extension("dir.v2/REPORT.PDF") == ".pdf"
    assert file_extension("dir.v2/noext") == ""
    assert file_extension("a.tar.gz") == ".gz"


def test_extracted_text_nests_under_the_originals_directory_and_flat_keys_get_no_directory():
    assert text_blob_path("kb/sub/a.pdf", "u1") == "kb/sub/texts/u1.txt"
    assert text_blob_path("a.pdf", "u1") == "texts/u1.txt"


@pytest.mark.parametrize("reference, expected", [
    ("kb/a.pdf", "kb/a.pdf"),
    ("/kb/a.pdf", "kb/a.pdf"),
    ("https://acct.blob.core.windows.net/kb/a.pdf", "a.pdf"),
    ("https://acct.blob.core.windows.net/other/a.pdf", "other/a.pdf"),
])
def test_a_blob_name_is_container_relative_whether_given_a_url_or_a_path(reference, expected):
    assert blob_name(reference, "kb") == expected


@pytest.mark.parametrize("reference, expected", [
    ("kb/a.txt", "kb/a.txt"),
    ("  /kb/a.txt ", "kb/a.txt"),
    (r"kb\a.txt", "kb/a.txt"),
    ("https://acct.blob.core.windows.net/kb/a.txt", "kb/a.txt"),
    ("../x", "../x"),
])
def test_a_volume_path_takes_the_url_path_and_normalises_separators(reference, expected):
    assert volume_relative(reference) == expected


@pytest.mark.parametrize("reference", ["", "   ", None])
def test_an_empty_volume_reference_is_refused(reference):
    with pytest.raises(ValueError):
        volume_relative(reference)


def test_a_directory_mode_is_read_as_octal_with_or_without_a_prefix():
    assert parse_dir_mode("770") == 0o770
    assert parse_dir_mode(" 0755 ") == 0o755
    assert parse_dir_mode("0o700") == 0o700
    assert parse_dir_mode("0") == 0


@pytest.mark.parametrize("bad", ["", "abc", "778", "1000", "-1", "7.5"])
def test_a_directory_mode_that_is_not_an_octal_permission_is_refused(bad):
    with pytest.raises(ValueError):
        parse_dir_mode(bad)
