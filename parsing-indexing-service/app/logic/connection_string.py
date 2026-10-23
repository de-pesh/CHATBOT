"""The ADO-style DB_CONNECTION_STRING, "Server=host[,port];UserID=..;Password=..;Database=..", shared by every database backend."""
_DEFAULTS = {"user": "root", "password": "", "database": ""}
_REQUIRED_LABELS = {"user": "UserID/Uid", "password": "Password/Pwd", "database": "Database/Initial Catalog"}


def connection_parts(connection_string: str) -> dict:
    parts = {}
    for item in connection_string.split(";"):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            parts[key.strip().lower()] = value.strip()
    return parts


def parse_connection_string(connection_string: str, required: bool = False) -> dict:
    parts = connection_parts(connection_string)

    def pick(*aliases: str) -> str | None:
        return next((parts[alias] for alias in aliases if alias in parts), None)

    host = pick("server", "data source") or "localhost"
    port = pick("port")
    if "," in host:
        host, _, port = host.partition(",")
    found = {
        "user": pick("userid", "uid", "user id", "user"),
        "password": pick("password", "pwd"),
        "database": pick("database", "initial catalog"),
    }
    if required:
        missing = [_REQUIRED_LABELS[key] for key, value in found.items() if value is None]
        if missing:
            raise ValueError(
                f"DB_CONNECTION_STRING is missing required part(s): {', '.join(missing)}. "
                "Expected 'Server=host,port;UserID=user;Password=pass;Database=db' - "
                "keys are case-insensitive and extra whitespace around ';' is fine."
            )
    return {
        "host": host.strip(),
        "port": int(port) if port else None,
        **{key: _DEFAULTS[key] if value is None else value for key, value in found.items()},
    }
