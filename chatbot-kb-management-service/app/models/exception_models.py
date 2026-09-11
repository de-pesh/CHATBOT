from typing import Optional


class LLMInteractionError(Exception):
    """Custom exception for errors during LLM interaction."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self):
        return f"{super().__str__()} (Status Code: {self.status_code}, Response: {self.response_body or 'N/A'})"


class Neo4jServiceError(Exception):
    """Custom exception for errors related to the Neo4jService."""
    def __init__(self, message: str, original_exception: Optional[Exception] = None):
        super().__init__(message)
        self.original_exception = original_exception

    def __str__(self):
        if self.original_exception:
            return f"{super().__str__()} (Original exception: {type(self.original_exception).__name__}: {self.original_exception})"
        return super().__str__()


class SessionNotFoundException(Exception):
    """
    A class to raise exception of Session not found.
    """
    def __init__(
        self,
        token: str
    ):
        super().__init__(f"Session for token: {token} not found")
