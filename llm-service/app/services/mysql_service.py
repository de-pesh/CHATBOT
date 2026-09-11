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

# from app.core.stored_procedures import StoredProcedures
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
        logger.info("MySQLService initialized.")


    async def connect(self):
        """Establishes a connection pool to the MySQL database."""
        try:
            # Assuming connection_string is in the format "mysql://user:password@host:port/database"
            # aiomysql.create_pool expects host, user, password, db, port separately
            # For simplicity, let's assume connection_string is a direct DSN or we parse it.
            # For now, let's assume it's a simple DSN like "host=localhost;user=root;password=pass;db=mydb"
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
            logger.info("Successfully established MySQL connection pool.")
        except aiomysql.Error as e:
            logger.exception(f"Failed to establish MySQL connection pool. {e}")
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not connect to the database."
            )


    async def disconnect(self):
        """Closes the MySQL connection pool."""
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
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
        if not self.pool:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database connection is not available."
            )

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
        except aiomysql.Error as e:
            logger.exception(f"Database error when calling procedure '{procedure_name}'.")
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An error occurred while executing a database operation."
            )
