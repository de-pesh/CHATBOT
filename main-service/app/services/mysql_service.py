"""
Concrete implementation of the DatabaseService for MySQL.

This module provides a production-grade service for interacting with a
MySQL database, including support for calling stored procedures.
"""
import logging
from typing import Any, List, Optional

import aiomysql
from fastapi import HTTPException
from starlette import status as http_status

from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class MySQLService(DatabaseService):
    """
    A concrete implementation of the DatabaseService for MySQL.

    This service handles the connection and execution of stored procedures
    for a MySQL database.
    """

    def __init__(self, connection_string: str):
        """
        Initializes the MySQLService.

        Args:
            connection_string (str): The connection string for the MySQL database.
        """
        self.connection_string = connection_string
        self.pool = None
        self._is_initialized = False
        logger.info("MySQLService initialized.")


    async def initialize(self):
        """Establishes a connection pool to the MySQL database."""
        if self._is_initialized:
            logger.warning("MySQL connection pool already initialized.")
            return
        try:
            # Assuming connection_string is in the format "mysql://user:password@host:port/database"
            # aiomysql.create_pool expects host, user, password, db, port separately
            parts = dict(item.split('=', 1) for item in self.connection_string.split(';') if '=' in item and item.strip())

            # Map the parsed parts to aiomysql.create_pool arguments
            host = parts.get('Server', 'localhost')
            user = parts.get('UserID', 'root')
            password = parts.get('Password', '')
            db = parts.get('Database', '')

            # Handle SslMode
            ssl_mode = parts.get('SslMode', '').lower()
            ssl_args = None
            if ssl_mode == 'required':
                ssl_args = True # aiomysql might accept True for basic SSL

            self.pool = await aiomysql.create_pool(
                host=host,
                user=user,
                password=password,
                db=db,
                autocommit=True,
                ssl=ssl_args
            )
            self._is_initialized = True
            logger.info("Successfully established MySQL connection pool.")
        except aiomysql.Error as e:
            logger.exception(f"Failed to establish MySQL connection pool. {e}")
            self._is_initialized = False
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not connect to the database."
            )

    async def reinitialize(self):
        """Re-initializes the connection to the database."""
        logger.info("Re-initializing database connection...")
        await self.close()
        await self.initialize()

    def _ensure_initialized(self):
        """Ensures that the database connection is initialized."""
        if not self._is_initialized or not self.pool:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database connection is not available."
            )

    async def close(self):
        """Closes the MySQL connection pool."""
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
            self.pool = None
            self._is_initialized = False
            logger.info("MySQL connection pool closed.")


    async def call_procedure(
        self,
        procedure_name: str,
        parameters: Optional[List[Any]] = None
    ) -> List[List[dict[str, Any]]]:
        """
        Calls a stored procedure in the MySQL database.

        Args:
            procedure_name (str): The name of the stored procedure, which must be a valid
                                  attribute of the StoredProcedures class.
            parameters (Optional[List[Any]]): A list of parameters for the procedure.

        Returns:
            List[List[Dict[str, Any]]]: A list of lists of dictionaries, where each inner list
                                       represents a result set returned by the procedure.

        Raises:
            ValueError: If the procedure_name is not a valid stored procedure.
        """
        self._ensure_initialized()

        # MySQL stored procedures are called using CALL syntax
        sql = f"CALL {procedure_name}(" + ", ".join(["%s"] * len(parameters or [])) + ")"
        logger.info(f"Calling stored procedure: {procedure_name}")
        logger.debug(f"Executing SQL: {sql} with parameters: {parameters}")

        try:
            async with self.pool.acquire() as conn:
                async with conn.cursor(aiomysql.cursors.DictCursor) as cursor: # Use DictCursor for dict results
                    await cursor.execute(sql, parameters or [])

                    all_results = []
                    while True:
                        rows = await cursor.fetchall()
                        all_results.append(rows)
                        if not await cursor.nextset():
                            break
                    return all_results
        except aiomysql.OperationalError as e:
            logger.exception(f"Database connection error when calling procedure '{procedure_name}'. Attempting to reinitialize connection. Error: {e}")
            await self.reinitialize()
            # After re-initializing, retry the call once
            async with self.pool.acquire() as conn:
                async with conn.cursor(aiomysql.cursors.DictCursor) as cursor:
                    await cursor.execute(sql, parameters or [])

                    all_results = []
                    while True:
                        rows = await cursor.fetchall()
                        all_results.append(rows)
                        if not await cursor.nextset():
                            break
                    return all_results
        except aiomysql.Error as e:
            logger.exception(f"Database error when calling procedure '{procedure_name}'.")
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while executing a database operation."
            )

    async def __aenter__(self):
        """Initializes the database connection when entering an 'async with' block."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Closes the database connection when exiting an 'async with' block."""
        await self.close()

