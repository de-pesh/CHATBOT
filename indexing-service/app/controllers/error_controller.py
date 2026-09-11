import aiohttp


async def add_error_log(
        analysis_id: str,
        message: str,
        http_client: aiohttp.ClientSession,
        operation_id: str,
        db_service_url: str
):
    """
    Asynchronously logs an error message to an external database service for a given analysis.

    Args:
        analysis_id (str): The ID of the analysis to associate the log with.
        message (str): The error message to log.
        http_client (aiohttp.ClientSession): The aiohttp client session to use for the HTTP request.
        operation_id (str): The operation ID related to the error.
        db_service_url (str): The base URL of the external database logging service.

    Returns:
        None

    Prints:
        Success or failure messages for debugging purposes.
    """
    try:
        url_path = f"{db_service_url}/analysis/{analysis_id}/logs"

        async with http_client.patch(url_path, json={"service": "indexing_service", "operation_id": operation_id, "message": message}, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status >= 400:
                print(f"AsyncDBLogHandler: Failed to log to DB (status {response.status}) for analysis {analysis_id}. URL: {url_path}. Response: {await response.text()}")
            else:
                print(f"AsyncDBLogHandler: Successfully logged to DB for analysis {analysis_id}") # For debugging
    except aiohttp.ClientConnectorError as e:
        print(f"AsyncDBLogHandler: Connection error for DB logging for analysis {analysis_id}: {e}")
