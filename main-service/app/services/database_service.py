"""
Module for the Abstract Base Class for database services.

This module defines the abstract interface that all concrete database
service implementations must follow.
"""
from abc import ABC, abstractmethod
from typing import Any, List, Optional


class DatabaseService(ABC):
    """
    Abstract Base Class for a database service.

    Defines the common interface that all concrete database service
    implementations must adhere to. This allows the application to
    interact with different database systems through a unified API.
    """

    @abstractmethod
    async def initialize(self):
        """Establishes a connection to the database."""
        pass

    @abstractmethod
    async def reinitialize(self):
        """Re-initializes the connection to the database."""
        pass

    @abstractmethod
    async def close(self):
        """Closes the connection to the database."""
        pass

    @abstractmethod
    async def call_procedure(
        self,
        procedure_name: str,
        parameters: Optional[List[Any]] = None
    ) -> List[dict[str, Any]]:
        """
        Calls a stored procedure in the database.

        Args:
            procedure_name (str): The name of the stored procedure to execute.
            parameters (Optional[List[Any]]): A list of parameters to pass
                                              to the procedure in order.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, where each dictionary
                                  represents a row returned by the procedure.
        """
        pass
