"""Backend-neutral message queue interface.

One queue is in play - `chatbot_queue`, produced by the file router and
consumed by `kb_worker` in the same process - but it is the seam between an
upload returning and the indexing that follows it, so it gets an interface like
the other infra.

A backend must provide ordinary priority-queue semantics plus at-least-once
delivery with a dead-letter queue: `run_worker` re-queues a failed task up to
`max_retries` times and then moves it to `dlq_name`.
"""
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

# Lower is consumed first. What send_message uses when the caller names no priority.
DEFAULT_PRIORITY = 100.0

# `async def process(payload: Dict[str, Any], operation_id: str) -> None`
# It must raise on failure - that is how the worker learns to retry.
ProcessFuncType = Callable[..., Awaitable[None]]

# `async def on_dead_letter(payload: Dict[str, Any], operation_id: str, error_message: str) -> None`
# Called once, when a task has exhausted max_retries and is being moved to the
# DLQ - the only point at which the queue itself knows a task has permanently
# failed rather than just failed-so-far. A caller that surfaces job status
# somewhere (e.g. a DB row) hooks in here instead of guessing from silence.
DeadLetterFuncType = Callable[..., Awaitable[None]]


class MessageQueueService(ABC):
    """Abstract Base Class for a message queue backend."""

    @abstractmethod
    async def initialize(self) -> None:
        """Connects to the broker and verifies the connection."""

    @abstractmethod
    async def close(self) -> None:
        """Closes the connection."""

    @abstractmethod
    async def send_message(
        self,
        payload: dict[str, Any],
        priority: float = DEFAULT_PRIORITY,
        operation_id: str | None = None,
    ) -> None:
        """Enqueues one task. Lower `priority` is consumed first."""

    @abstractmethod
    async def run_worker(
        self,
        process_func: ProcessFuncType,
        brpop_timeout: int,
        error_retry_delay: int,
        on_dead_letter: DeadLetterFuncType | None = None,
    ) -> None:
        """Consumes until `close()` is called or the task is cancelled. Retries and dead-letters failures.

        `brpop_timeout` is how long one blocking pop waits for a message, and
        `error_retry_delay` the wait before retrying after a broker error and
        between a failed task's retries (Settings.WORKER_BRPOP_TIMEOUT /
        WORKER_ERROR_RETRY_DELAY). `on_dead_letter`, if given, is awaited exactly
        once per task that exhausts its retries - the caller's chance to record a
        permanent failure somewhere a status check can see it.
        """
