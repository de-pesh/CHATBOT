from enum import Enum

class StoredProcedures(str, Enum):
    """
    Enum of stored procedure names used by the application services.
    """
    # --- File Operations (Existing) ---
    UPSERT_FILE = "upsert_file"
    DELETE_FILE = "delete_file"