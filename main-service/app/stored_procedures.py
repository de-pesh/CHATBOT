"""
Defines constants for stored procedure names.

This module centralizes the names of stored procedures used throughout
the application, ensuring consistency and reducing the chance of typos.

It used to list 141 constants for the assessment, framework and RBAC domains;
those went with their routers. What is left is the file, chat and user set.
"""


class StoredProcedures:
    """A collection of constants representing stored procedure names."""

    # Files - the knowledge-base ingestion path.
    UPSERT_FILE = "upsert_file"
    FILTER_FILES = "filter_files"
    DELETE_FILE = "delete_file"
    GET_FILES_BY_ENTITY = "get_files_by_entity"

    # Chat sessions and history.
    UPSERT_CHATBOT_SESSION = "upsert_chatbot_session"
    FILTER_CHATBOT_SESSIONS = "filter_chatbot_sessions"
    DELETE_CHATBOT_SESSION = "delete_chatbot_session"
    UPSERT_CHATBOT_HISTORY = "upsert_chatbot_history"
    FILTER_CHATBOT_HISTORY = "filter_chatbot_history"

    # Users and permissions, for auth.
    #
    # WARNING: these three are not defined in any .sql file in this repo and
    # there is no users table, so every call to them fails. Auth is currently
    # switched off via DISABLE_AUTH=True, which is the only reason the service
    # runs at all. See the note in GUIDE.md.
    FILTER_USERS = "filter_users"
    UPSERT_USER = "upsert_user"
    GET_USER_PERMISSIONS = "filter_user_permissions"
