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