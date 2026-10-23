"""The ingestion jobs on the queue: what a task must carry, and the rows and metadata it produces."""
from enum import StrEnum

from app.logic.queue_codec import task_timeout


class PolicyActionType(StrEnum):
    CREATE = "create"


def is_create(payload: dict) -> bool:
    return payload.get("action_type") == PolicyActionType.CREATE


def file_id_of(payload: dict):
    """The file record a CREATE task ingests into. Raises ValueError if the task names none."""
    file_id = payload.get("file_id")
    if file_id is None:
        raise ValueError("file_id must be provided for CREATE action.")
    return file_id


def should_ingest(row_exists: bool, row_is_active: bool) -> bool:
    """Whether a CREATE task still has a file to ingest into.

    A deleted file (no row, or an inactive one) must not be resurrected: writing its extracted text path and
    vector_store_file_id back would make upsert_file mark it active again, and the indexed chunks would outlive
    the delete that already ran.
    """
    return row_exists and row_is_active


def stale_chunk_sets(seen_ids: list, indexed_id: str | None, recorded: bool) -> list[str]:
    """The knowledge-base documents (chunk sets) an ingest attempt should delete once it is over.

    Every attempt indexes a fresh document, and only the one recorded on the file row is reachable
    afterwards - deleting the file deletes only that one - so anything else is an orphan that keeps
    turning up in search. `seen_ids` are the vector_store_file_ids the row held when this attempt looked
    (before it started, and again just before it wrote).

    - not recorded (the attempt failed, was cancelled, or the file was deleted meanwhile): the
      document this attempt indexed, if it got that far;
    - recorded: the older documents the row pointed at, which the new one replaces.
    """
    if not recorded:
        return [indexed_id] if indexed_id else []
    return list(dict.fromkeys(i for i in seen_ids if i and i != indexed_id))


def source_path(payload: dict) -> str:
    """Where the uploaded file is stored. Raises ValueError if the task has none."""
    path = payload.get("file_path")
    if path is None:
        raise ValueError("file_path must be provided for CREATE action.")
    return path


def file_fields(payload: dict) -> tuple[str, str, str]:
    """(file name, mimetype, uploading user) of a CREATE task. Raises ValueError naming what is missing."""
    name = payload.get("name") or payload.get("file_name")
    mimetype = payload.get("mimetype")
    user_id = payload.get("user_id")
    if not name:
        raise ValueError("File name must be provided in payload for CREATE action.")
    if not mimetype:
        raise ValueError("MIME type must be provided in payload for CREATE action.")
    if not user_id:
        raise ValueError("user_id must be provided in payload for CREATE action.")
    return name, mimetype, user_id


VERSION_FIELDS = ("document_key", "version_no", "version_label")


def index_metadata(file_id, file_name: str, payload: dict | None = None) -> dict:
    """The metadata indexed with each chunk.

    `file_name` is the only human-readable, citable document name the chat pipeline
    ever sees (the parsed text's own path looks nothing like it), and citations are
    resolved back to a file record by that exact name. `source_file_id` is inert: the
    indexer mints its own id, which comes back as `indexed_id` and is what actually
    ties the vector chunks to the SQL row.
    """
    metadata = {"source_file_id": str(file_id), "file_name": file_name}
    if payload and payload.get("document_key"):
        # A versioned upload. Its chunks start out NOT latest, so they are invisible to a search until the file
        # record is updated and the latest version is elected (controllers/version_controller.sync_document).
        metadata.update({name: payload[name] for name in VERSION_FIELDS if payload.get(name) is not None})
        metadata["is_latest"] = False
        if payload.get("file_path"):
            metadata["stored_file_path"] = payload["file_path"]  # so a citation links to THIS version's file
    return metadata


def file_record_params(file_id, name: str, mimetype: str, file_path: str, extracted_text_path: str, indexed_id: str, user_id: str) -> list:
    """upsert_file, updating an existing row: the extracted text path and vector_store_file_id are the new information."""
    return [file_id, name, mimetype, file_path, extracted_text_path, True, indexed_id, user_id]


def failure_reason(error_message: str, max_chars: int) -> str:
    """The part of a task's error kept on the file record; the full message is still logged."""
    return error_message[:max_chars]


# A task makes two compute-service calls (parse, then index), each bounded by RPC_TIMEOUT_SECONDS.
RPC_CALLS_PER_TASK = 2


def lease_covers_processing(lease_seconds: float, rpc_timeout_seconds: float) -> bool:
    """False when the time a worker lets one task run (see `task_timeout`) is shorter than its
    worst-case compute-service time: a healthy but slow task would be abandoned and retried."""
    return task_timeout(lease_seconds) > RPC_CALLS_PER_TASK * rpc_timeout_seconds
