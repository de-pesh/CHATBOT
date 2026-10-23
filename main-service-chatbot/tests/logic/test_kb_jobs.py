"""What an ingestion task must carry, and what it produces."""
import pytest
from app.logic.kb_jobs import (
    PolicyActionType,
    failure_reason,
    file_fields,
    file_id_of,
    file_record_params,
    index_metadata,
    is_create,
    lease_covers_processing,
    should_ingest,
    source_path,
    stale_chunk_sets,
)

TASK = {"action_type": "create", "file_id": 7, "file_path": "abc_a.pdf", "name": "a.pdf", "mimetype": "application/pdf", "user_id": "u@x"}


def test_only_a_create_task_is_understood():
    assert is_create(TASK)
    assert is_create({"action_type": PolicyActionType.CREATE})
    assert not is_create({"action_type": "delete"})
    assert not is_create({})


def test_the_stored_path_is_required():
    assert source_path(TASK) == "abc_a.pdf"
    with pytest.raises(ValueError, match="file_path must be provided"):
        source_path({})


def test_the_file_fields_come_from_the_task():
    assert file_fields(TASK) == ("a.pdf", "application/pdf", "u@x")
    assert file_fields({**TASK, "name": None, "file_name": "b.pdf"})[0] == "b.pdf"


@pytest.mark.parametrize("missing,message", [
    ("name", "File name must be provided"),
    ("mimetype", "MIME type must be provided"),
    ("user_id", "user_id must be provided"),
])
def test_each_missing_field_is_named(missing, message):
    with pytest.raises(ValueError, match=message):
        file_fields({**TASK, missing: None})


def test_the_indexed_metadata_carries_the_citable_name():
    assert index_metadata(7, "a.pdf") == {"source_file_id": "7", "file_name": "a.pdf"}


def test_the_file_row_update_carries_the_new_paths_in_upsert_order():
    assert file_record_params(7, "a.pdf", "application/pdf", "abc_a.pdf", "abc_a.txt", "kb-1", "u@x") == [
        7, "a.pdf", "application/pdf", "abc_a.pdf", "abc_a.txt", True, "kb-1", "u@x",
    ]


def test_a_failure_reason_is_cut_to_the_column():
    assert failure_reason("x" * 50, 10) == "x" * 10
    assert failure_reason("short", 10) == "short"


def test_a_task_must_be_allowed_longer_than_both_compute_service_calls():
    assert lease_covers_processing(900, 60)  # the defaults: 840s for two 60s calls
    assert not lease_covers_processing(130, 60)  # 117s allowed, 120s needed
    assert lease_covers_processing(140, 60)
    assert not lease_covers_processing(60, 60)


def test_a_task_must_name_its_file():
    assert file_id_of(TASK) == 7
    assert file_id_of({"file_id": 0}) == 0
    with pytest.raises(ValueError, match="file_id"):
        file_id_of({"file_id": None})
    with pytest.raises(ValueError, match="file_id"):
        file_id_of({})


def test_only_a_file_that_still_exists_and_is_active_is_ingested():
    assert should_ingest(True, True)
    assert not should_ingest(True, False)  # deleted (soft)
    assert not should_ingest(False, False)  # row gone


def test_an_attempt_that_did_not_record_its_document_discards_it():
    assert stale_chunk_sets(["kb-old"], "kb-new", recorded=False) == ["kb-new"]
    assert stale_chunk_sets([], None, recorded=False) == []  # it never got as far as indexing


def test_an_attempt_that_recorded_its_document_discards_the_ones_it_replaced():
    assert stale_chunk_sets(["kb-old"], "kb-new", recorded=True) == ["kb-old"]
    assert stale_chunk_sets(["kb-old", "kb-x"], "kb-new", recorded=True) == ["kb-old", "kb-x"]


def test_nothing_is_discarded_that_is_still_needed_or_was_never_there():
    assert stale_chunk_sets([None, None], "kb-new", recorded=True) == []
    assert stale_chunk_sets(["kb-new"], "kb-new", recorded=True) == []  # never delete what was just recorded
    assert stale_chunk_sets(["kb-old", "kb-old", None], "kb-new", recorded=True) == ["kb-old"]


def test_a_versioned_uploads_chunks_start_out_not_latest_and_carry_their_own_file():
    from app.logic.kb_jobs import index_metadata

    payload = {"document_key": "policy", "version_no": 2, "version_label": "3.2", "file_path": "u/2_Policy.pdf"}
    assert index_metadata(7, "Policy.pdf", payload) == {
        "source_file_id": "7", "file_name": "Policy.pdf", "document_key": "policy", "version_no": 2,
        "version_label": "3.2", "is_latest": False, "stored_file_path": "u/2_Policy.pdf",
    }


def test_an_upload_without_version_fields_is_indexed_exactly_as_before():
    from app.logic.kb_jobs import index_metadata

    assert index_metadata(7, "Policy.pdf") == {"source_file_id": "7", "file_name": "Policy.pdf"}
    assert index_metadata(7, "Policy.pdf", {"file_path": "x"}) == {"source_file_id": "7", "file_name": "Policy.pdf"}
