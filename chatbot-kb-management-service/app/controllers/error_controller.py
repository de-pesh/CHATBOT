import aiohttp


async def add_error_log(
        operation_id: str,
        message: str,
        http_client: aiohttp.ClientSession,
        db_service_url: str
):
    """
    Asynchronously logs an error message to an external database service for chatbot operations.

    Args:
        operation_id (str): The operation or task ID related to the error.
        message (str): The error message to log.
        http_client (aiohttp.ClientSession): The aiohttp client session to use for the HTTP request.
        db_service_url (str): The base URL of the external database logging service.

    Returns:
        None

    Prints:
        Success or failure messages for debugging purposes.
    """
    try:
        url_path = f"{db_service_url}/chatbotKB/logs"

        async with http_client.patch(url_path, json={"service": "chatbot_service", "operation_id": operation_id, "message": message}, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status >= 400:
                print(f"AsyncDBLogHandler: Failed to log to DB (status {response.status}). URL: {url_path}. Response: {await response.text()}")
            else:
                print(f"AsyncDBLogHandler: Successfully logged to DB.") # For debugging
    except aiohttp.ClientConnectorError as e:
        print(f"AsyncDBLogHandler: Connection error for DB logging: {e}")
