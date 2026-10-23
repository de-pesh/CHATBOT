"""
Defines constants for stored procedure names.

This module centralizes the names of stored procedures used throughout
the application, ensuring consistency and reducing the chance of typos.
"""


# page_size that asks a filter_* procedure for every row in one page.
ALL_ROWS = 1_000_000


class StoredProcedures:
    """A collection of constants representing stored procedure names."""

    # Files - the knowledge-base ingestion path.
    UPSERT_FILE = "upsert_file"
    FILTER_FILES = "filter_files"
    DELETE_FILE = "delete_file"
    UPDATE_FILE_PROCESSING_STATUS = "update_file_processing_status"
    UPDATE_FILE_STATUS = "update_file_status"  # 13_file_lifecycle_status.sql
    SET_FILE_CONTENT_HASH = "set_file_content_hash"
    FIND_FILE_BY_CONTENT_HASH = "find_file_by_content_hash"
    LIST_FILES_MISSING_HASH = "list_files_missing_hash"  # 12_files_missing_hash.sql
    LIST_STUCK_FILES = "list_stuck_files"               # 16_stuck_file_recovery.sql

    # Document versions - scripts/mssql/10_document_versions.sql.
    ASSIGN_FILE_VERSION = "assign_file_version"  # 11_atomic_version_assignment.sql
    PROMOTE_LATEST_VERSION = "promote_latest_version"
    LIST_DOCUMENT_VERSIONS = "list_document_versions"

    # Admin audit trail, per-turn chat trace and the metrics over them - scripts/mssql/09_audit_trace_hash.sql.
    INSERT_AUDIT_LOG = "insert_audit_log"
    FILTER_AUDIT_LOG = "filter_audit_log"
    UPSERT_CHATBOT_TRACE = "upsert_chatbot_trace"
    GET_CHATBOT_TRACE = "get_chatbot_trace"
    CHAT_METRICS = "chat_metrics"
    PURGE_AUDIT_AND_TRACE = "purge_audit_and_trace"

    # Chat sessions and history. Ownership (owner_id) points at dbo.users - see
    # scripts/mssql/04_chat_user_links.sql and app.dependencies.chat_owner_id.
    UPSERT_CHATBOT_SESSION = "upsert_chatbot_session"
    FILTER_CHATBOT_SESSIONS = "filter_chatbot_sessions"
    DELETE_CHATBOT_SESSION = "delete_chatbot_session"
    UPSERT_CHATBOT_HISTORY = "upsert_chatbot_history"
    FILTER_CHATBOT_HISTORY = "filter_chatbot_history"

    # Thumbs up/down on one chat turn - scripts/mssql/06_chatbot_feedback.sql.
    UPSERT_CHATBOT_FEEDBACK = "upsert_chatbot_feedback"
    FILTER_CHATBOT_FEEDBACK = "filter_chatbot_feedback"

    # Citation grounding check, run after the answer is sent - scripts/mssql/07_chatbot_grounding.sql.
    UPDATE_CHATBOT_HISTORY_GROUNDING = "update_chatbot_history_grounding"

    # Users, for auth and for what the chat prompts know about the caller (role, display_name,
    # preferences - scripts/mssql/05_users.sql). role is a plain 'admin'/'user' column, not a
    # join - see app.dependencies.require_admin. DISABLE_AUTH is still True by default (see
    # Settings.DISABLE_AUTH) - flipping it off requires a real account, created with
    # scripts/create_superuser.py.
    FILTER_USERS = "filter_users"
    UPSERT_USER = "upsert_user"
    UPDATE_USER_PROFILE = "update_user_profile"

    # DMS document tables - scripts/mssql/17_dms_and_tenant_context.sql.
    FILTER_DMS_DOCUMENTS = "filter_dms_documents"
    FILTER_DMS_DOCUMENT_VERSIONS = "filter_dms_document_versions"
    GET_DMS_DOCUMENT_VERSION = "get_dms_document_version"
