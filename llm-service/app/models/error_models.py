'''
Module for defining error models and exception handlers.

This module provides standardized error response models and global exception 
handlers for FastAPI applications. It ensures consistent error handling 
throughout the application.
'''

class SessionNotFoundException(Exception):
    """
    A class to raise exception of Session not found.
    """
    def __init__(
        self,
        token: str
    ):
        super().__init__(f"Session for token: {token} not found")


class LLMInteractionError(Exception):
    """Custom exception for errors during LLM interaction."""
    def __init__(self, message: str, status_code: int = None, response_body: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self):
        response_preview = self.response_body[:200] if self.response_body else "None"
        return f"{super().__str__()} (Status: {self.status_code}, Response: {response_preview}...)"
