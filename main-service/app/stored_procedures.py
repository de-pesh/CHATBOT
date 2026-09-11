"""
Defines constants for stored procedure names.

This module centralizes the names of stored procedures used throughout
the application, ensuring consistency and reducing the chance of typos.

It used to list 141 constants for the assessment, framework and RBAC domains.
Only the seven below are called now; the rest went with those routers.
"""


class StoredProcedures:
    """A collection of constants representing stored procedure names."""

    # Files - the knowledge-base ingestion path.
    UPSERT_FILE = "upsert_file"
    FILTER_FILES = "filter_files"
    DELETE_FILE = "delete_file"
    GET_FILES_BY_ENTITY = "get_files_by_entity"

    # Users and permissions, for auth.
    #
    # WARNING: these three are not defined in any .sql file in this repo and
    # there is no users table, so every call to them fails. Auth is currently
    # switched off via DISABLE_AUTH=True, which is the only reason the service
    # runs at all. See the note in GUIDE.md.
    FILTER_USERS = "filter_users"
    UPSERT_USER = "upsert_user"
    GET_USER_PERMISSIONS = "filter_user_permissions"
