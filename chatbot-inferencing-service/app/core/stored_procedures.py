"""
Defines constants for stored procedure names.

This module centralizes the names of stored procedures used throughout
the application, ensuring consistency and reducing the chance of typos.
"""

class StoredProcedures:
    """
    A collection of constants representing stored procedure names.
    """
    # Chat History Stored Procedures
    # Used by the KB queue worker to record a file's parsed path.
    UPSERT_FILE = "upsert_file"
    DELETE_FILE = "delete_file"

    UPSERT_CHATBOT_HISTORY = "upsert_chatbot_history"
    FILTER_CHATBOT_HISTORY = "filter_chatbot_history"

    # Chat Session Stored Procedures
    UPSERT_CHATBOT_SESSION = "upsert_chatbot_session"
    FILTER_CHATBOT_SESSIONS = "filter_chatbot_sessions"
    DELETE_CHATBOT_SESSION = "delete_chatbot_session"