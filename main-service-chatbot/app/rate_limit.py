"""The per-user chat rate limit (CHAT_RATE_LIMIT_PER_MINUTE): a FastAPI dependency on POST /chat and /chat/stream."""
import logging
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings
from app.dependencies import CurrentUserDep

logger = logging.getLogger(__name__)

_chat_turns: dict[str, deque] = defaultdict(deque)  # user -> monotonic times of turns in the last minute
RATE_WINDOW_SECONDS = 60


def _too_many(retry_after: int) -> HTTPException:
    retry_after = max(1, retry_after)
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many messages; try again in {retry_after} seconds.",
        headers={"Retry-After": str(retry_after)},
    )


def _limit_in_memory(email: str, limit: int) -> None:
    """Sliding one-minute window in this process."""
    now = time.monotonic()
    turns = _chat_turns[email]
    while turns and now - turns[0] >= RATE_WINDOW_SECONDS:
        turns.popleft()
    if len(turns) >= limit:
        raise _too_many(int(RATE_WINDOW_SECONDS - (now - turns[0])) + 1)
    turns.append(now)


async def chat_rate_limit(
    request: Request, current_user: CurrentUserDep, settings: Settings = Depends(get_settings),
) -> None:
    """429 once a user has started CHAT_RATE_LIMIT_PER_MINUTE turns this minute.

    Counted in Redis (a fixed one-minute window) so the limit holds across replicas; if Redis is
    unavailable, or the queue is not a Redis one, it is counted in this process instead - a per-replica
    limit is better than none, and a Redis blip must not stop anyone chatting."""
    limit = settings.CHAT_RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return
    queue = getattr(request.app.state, "redis_chatbot_queue", None)
    counter = getattr(queue, "count_in_window", None)
    if counter is not None:
        try:
            count = await counter(f"chat:{current_user.email}", RATE_WINDOW_SECONDS)
        except Exception:
            logger.warning("Redis rate-limit counter failed; counting in memory.", exc_info=True)
        else:
            if count > limit:
                raise _too_many(RATE_WINDOW_SECONDS - int(time.time() % RATE_WINDOW_SECONDS))
            return
    _limit_in_memory(current_user.email, limit)
