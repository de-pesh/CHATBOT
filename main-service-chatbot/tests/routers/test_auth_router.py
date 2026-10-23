"""POST /auth/users: admin-only account creation, with the database stubbed."""
from datetime import datetime

import pytest
from app.config import Settings, get_settings
from app.dependencies import get_current_user, get_db_service
from app.main import app
from app.models.auth import CurrentUser
from app.services.db.stored_procedures import StoredProcedures
from fastapi.testclient import TestClient

NOW = datetime(2026, 1, 1).isoformat()

ADMIN = CurrentUser(
    id=1, first_name="Ada", last_name="Lovelace", email="admin@x.com", hashed_password="",
    role="admin", created_on=NOW, created_by="system",
)
PLAIN_USER = CurrentUser(
    id=2, first_name="Bob", last_name="B", email="bob@x.com", hashed_password="",
    role="user", created_on=NOW, created_by="system",
)


class FakeDB:
    """Mirrors the real shape: upsert_user returns only the new id (scripts/mssql/05_users.sql),
    so the controller re-reads the row via filter_users - see the by-id branch below."""

    def __init__(self, existing=None):
        self.calls = []
        self.existing = existing or []
        self.created_row = None

    async def call_procedure(self, name, params=None):
        self.calls.append((name, params))
        if name == StoredProcedures.FILTER_USERS:
            user_id = params[0]
            if user_id is not None:
                return [[self.created_row] if self.created_row else []]
            return [self.existing]
        if name == StoredProcedures.UPSERT_USER:
            _id, first_name, last_name, email, _hashed, role, is_active, actor = params
            self.created_row = {
                "id": 9, "first_name": first_name, "last_name": last_name, "email": email,
                "hashed_password": "hashed", "role": role, "is_active": is_active,
                "created_by": actor, "created_on": NOW, "updated_by": None, "updated_on": None,
            }
            return [[{"id": 9}]]
        return []

    def called(self, name):
        return [p for n, p in self.calls if n == name]


def _client(current_user, db):
    settings = Settings(
        _env_file=None, REDIS_URL="redis://localhost:6379/0", CHATBOT_QUEUE_NAME="q",
        DB_CONNECTION_STRING="x", SECRET_KEY="x", DISABLE_AUTH=False,
    )
    app.dependency_overrides.update({
        get_settings: lambda: settings,
        get_db_service: lambda: db,
        get_current_user: lambda: current_user,
    })
    test_client = TestClient(app)
    test_client.db = db
    return test_client


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_admin_can_create_a_user():
    db = FakeDB(existing=[])
    client = _client(ADMIN, db)

    response = client.post("/auth/users", json={
        "first_name": "New", "last_name": "Hire", "email": "new@x.com",
        "password": "at-least-8-chars", "role": "user",
    })

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 9
    assert body["email"] == "new@x.com"
    assert body["role"] == "user"
    assert "hashed_password" not in body
    assert "password" not in body

    upsert_params = db.called(StoredProcedures.UPSERT_USER)[0]
    assert upsert_params[0] is None  # new account, no id
    assert upsert_params[3] == "new@x.com"
    assert upsert_params[5] == "user"
    assert upsert_params[7] == "admin@x.com"  # created_by is the calling admin


def test_a_plain_user_cannot_create_a_user():
    db = FakeDB(existing=[])
    client = _client(PLAIN_USER, db)

    response = client.post("/auth/users", json={
        "first_name": "New", "last_name": "Hire", "email": "new@x.com",
        "password": "at-least-8-chars",
    })

    assert response.status_code == 403
    assert not db.called(StoredProcedures.UPSERT_USER)


def test_creating_a_duplicate_email_is_a_409():
    db = FakeDB(existing=[{
        "id": 5, "first_name": "Old", "last_name": "Hire", "email": "dupe@x.com",
        "hashed_password": "x", "role": "user", "is_active": True,
        "created_by": "system", "created_on": NOW, "updated_by": None, "updated_on": None,
    }])
    client = _client(ADMIN, db)

    response = client.post("/auth/users", json={
        "first_name": "New", "last_name": "Hire", "email": "dupe@x.com",
        "password": "at-least-8-chars",
    })

    assert response.status_code == 409
    assert not db.called(StoredProcedures.UPSERT_USER)
