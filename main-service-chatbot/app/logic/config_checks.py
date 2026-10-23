"""Startup checks on a settings object."""
from typing import Any


def require_settings(settings: Any, selector: str, *names: str) -> None:
    """Raises ValueError naming the settings in `names` that are unset or empty, and
    the `selector` setting (STORAGE_TYPE, EMBEDDING_TYPE, ...) that needs them."""
    missing = [name for name in names if not getattr(settings, name, None)]
    if missing:
        raise ValueError(f"{selector}={getattr(settings, selector)!r} requires {', '.join(missing)}.")
