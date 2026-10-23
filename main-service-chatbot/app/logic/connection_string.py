"""The ADO-style DB_CONNECTION_STRING, "Server=host[,port];UserID=..;Password=..;Database=..", shared by every database backend."""

_DEFAULTS = {"user": "root", "password": "", "database": ""}
_REQUIRED_LABELS = {"user": "UserID/Uid", "password": "Password/Pwd", "database": "Database/Initial Catalog"}


def connection_parts(connection_string: str) -> dict:
    """Every `key=value` pair, keys trimmed and lower-cased.

    Formatting is not meaningful: "Server=host; UserID=user; ..." (a space after
    ';', a natural way to hand-format the value) parses the same as the unspaced
    form. Keys this module does not map (Encrypt, SslMode, ...) are for the
    backend that understands them.
    """
    parts = {}
    for item in connection_string.split(";"):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            parts[key.strip().lower()] = value.strip()
    return parts


def parse_connection_string(connection_string: str, required: bool = False) -> dict:
    """{host, port, user, password, database}; port is None when the string names none.

    Accepts the usual ADO.NET aliases (Data Source, Uid/Pwd/"User Id", Initial
    Catalog) beside the documented Server/UserID/Password/Database, a
    "host,port" Server or a separate Port, and falls back to localhost, root and
    empty password/database - a string that does not parse as expected would
    otherwise fail later with a generic login error that gives no hint why.

    With `required`, a user, password or database that is genuinely absent (an
    empty password is a legitimate, if unusual, login) raises a ValueError
    naming what is missing instead of falling back.
    """
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
