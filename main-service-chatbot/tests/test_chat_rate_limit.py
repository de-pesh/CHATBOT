"""rate_limit.chat_rate_limit: a per-user one-minute limit, in Redis when there is one, else in memory."""
import asyncio
from types import SimpleNamespace

import pytest
from app import dependencies, rate_limit
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _clear_window():
    rate_limit._chat_turns.clear()
    yield
    rate_limit._chat_turns.clear()


def _call(email, limit, queue=None):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis_chatbot_queue=queue)))
    asyncio.run(rate_limit.chat_rate_limit(
        request, SimpleNamespace(email=email), SimpleNamespace(CHAT_RATE_LIMIT_PER_MINUTE=limit),
    ))


def test_blocks_the_turn_over_the_limit_with_retry_after():
    _call("a@x.com", 2)
    _call("a@x.com", 2)
    with pytest.raises(HTTPException) as exc:
        _call("a@x.com", 2)
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) >= 1


def test_limits_are_per_user_and_zero_disables():
    _call("a@x.com", 1)
    _call("b@x.com", 1)
    for _ in range(5):
        _call("c@x.com", 0)


class FakeRedisQueue:
    def __init__(self):
        self.counts = {}

    async def count_in_window(self, key, window_seconds):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


def test_redis_counter_is_used_when_the_queue_has_one():
    queue = FakeRedisQueue()
    _call("a@x.com", 1, queue)
    with pytest.raises(HTTPException) as exc:
        _call("a@x.com", 1, queue)
    assert exc.value.status_code == 429
    assert queue.counts == {"chat:a@x.com": 2}
    assert not rate_limit._chat_turns  # nothing was counted in memory


def test_a_failing_redis_falls_back_to_memory():
    class Broken:
        async def count_in_window(self, key, window_seconds):
            raise ConnectionError("redis down")

    _call("a@x.com", 1, Broken())
    with pytest.raises(HTTPException):
        _call("a@x.com", 1, Broken())


def test_admin_views_are_refused_while_auth_is_disabled():
    with pytest.raises(HTTPException) as exc:
        dependencies.require_real_accounts(SimpleNamespace(DISABLE_AUTH=True))
    assert exc.value.status_code == 403
    assert dependencies.require_real_accounts(SimpleNamespace(DISABLE_AUTH=False)) is None


def test_swagger_is_told_about_the_login_so_it_shows_an_authorize_button():
    from app.main import app

    schemes = app.openapi()["components"]["securitySchemes"]
    assert any(s.get("type") == "oauth2" and "password" in s.get("flows", {}) for s in schemes.values())


def test_a_bare_token_is_still_accepted_and_a_missing_one_is_none():
    from app.dependencies import oauth2_scheme

    def request(header):
        return SimpleNamespace(headers={"authorization": header} if header else {})

    assert oauth2_scheme(request("Bearer abc")) == "abc" and oauth2_scheme(request("abc")) == "abc"
    assert oauth2_scheme(request(None)) is None
