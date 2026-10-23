"""Backends, one subpackage per concern: llm, db, storage, queue, kb.

Each has an interface (or, for llm, a wrapper), a *_factory.py that picks the implementation from
config, and the implementations. Intentionally no re-exports: every import site names the module it
needs (`from app.services.db.mssql_service import MSSQLService`).
"""
