import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app # Import your FastAPI app
from app.services.user_service import UserService
from app.dependencies.user_service import get_user_service # To override dependency
from app.core.security import create_access_token, get_password_hash, verify_password

# Create a TestClient instance
client = TestClient(app)

# --- Mocks and Overrides ---

# Create a mock user service that we can control in our tests
mock_user_service = MagicMock(spec=UserService)

# This function will be used to override the `get_user_service` dependency
def override_get_user_service():
    return mock_user_service

# Apply the dependency override to the app
app.dependency_overrides[get_user_service] = override_get_user_service

# --- Test Data ---

TEST_USER_EMAIL = "test@example.com"
TEST_USER_PASSWORD = "strongpassword"
HASHED_PASSWORD = get_password_hash(TEST_USER_PASSWORD)

# A sample user record that the mock service would return
mock_user_data = {
    "user_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
    "profile": {
        "first_name": "Test",
        "last_name": "User",
        "email": TEST_USER_EMAIL,
        "phone_number": "1234567890",
        "location": {
            "address": "123 Main St",
            "pincode": "12345",
            "city": "Testville",
            "country": "Testland"
        }
    },
    "roles": [],
    "extra_permissions": [],
    "organization_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
    "organization_structure": [],
    "created_at": "2025-07-19T12:00:00Z",
    "updated_at": "2025-07-19T12:00:00Z",
    "hashed_password": HASHED_PASSWORD,
    "is_active": True
}

# --- Test Cases ---

@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset mocks before each test to ensure isolation."""
    mock_user_service.reset_mock()
    # Set up default mock behaviors here if needed
    mock_user_service.get_user_by_email = AsyncMock()


def test_login_for_access_token_success():
    """Tests a successful user login and token generation."""
    # Arrange: Configure the mock to return a valid user
    mock_user_service.get_user_by_email.return_value = mock_user_data

    # Act: Make a request to the login endpoint
    response = client.post(
        "/api/v1/token",
        data={"username": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}
    )

    # Assert: Check for a successful response and token
    assert response.status_code == 200
    json_response = response.json()
    assert "access_token" in json_response
    assert json_response["token_type"] == "bearer"
    mock_user_service.get_user_by_email.assert_awaited_once_with(TEST_USER_EMAIL)


def test_login_incorrect_password():
    """Tests a login attempt with an incorrect password."""
    # Arrange: Configure the mock to return a valid user
    mock_user_service.get_user_by_email.return_value = mock_user_data

    # Act: Make a request with the wrong password
    response = client.post(
        "/api/v1/token",
        data={"username": TEST_USER_EMAIL, "password": "wrongpassword"}
    )

    # Assert: Check for a 401 Unauthorized error
    assert response.status_code == 401
    json_response = response.json()
    assert json_response["detail"] == "Incorrect email or password"
    mock_user_service.get_user_by_email.assert_awaited_once_with(TEST_USER_EMAIL)


def test_login_user_not_found():
    """Tests a login attempt for a user that does not exist."""
    # Arrange: Configure the mock to return None (user not found)
    mock_user_service.get_user_by_email.return_value = None

    # Act: Make a request for a non-existent user
    response = client.post(
        "/api/v1/token",
        data={"username": "nonexistent@example.com", "password": "anypassword"}
    )

    # Assert: Check for a 401 Unauthorized error
    assert response.status_code == 401
    json_response = response.json()
    assert json_response["detail"] == "Incorrect email or password"
    mock_user_service.get_user_by_email.assert_awaited_once_with("nonexistent@example.com")


def test_login_inactive_user():
    """Tests a login attempt for an inactive user."""
    # Arrange: Configure the mock to return an inactive user
    inactive_user = mock_user_data.copy()
    inactive_user["is_active"] = False
    mock_user_service.get_user_by_email.return_value = inactive_user

    # Act: Make a request for the inactive user
    response = client.post(
        "/api/v1/token",
        data={"username": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}
    )

    # Assert: Check for a 400 Bad Request error
    assert response.status_code == 400
    json_response = response.json()
    assert json_response["detail"] == "Inactive user"
    mock_user_service.get_user_by_email.assert_awaited_once_with(TEST_USER_EMAIL)
