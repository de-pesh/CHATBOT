"""Splitting a T-SQL script into the batches a driver can execute one at a time."""
import re


def split_statements(text: str) -> list[str]:
    """Splits a script the way sqlcmd would: a line holding only GO ends a batch."""
    statements: list[str] = []
    current: list[str] = []

    def flush():
        body = "\n".join(current).strip()
        current.clear()
        if body and any(line.strip() and not line.strip().startswith("--") for line in body.splitlines()):
            statements.append(body)

    for line in text.splitlines():
        if re.fullmatch(r"\s*GO\s*", line, flags=re.IGNORECASE):
            flush()
        else:
            current.append(line)
    flush()
    return statements
