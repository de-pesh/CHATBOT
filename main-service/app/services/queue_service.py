"""Backend-neutral message queue interface.

One queue is in play - `chatbot_queue`, produced by the file and KB routers and
consumed by `kb_worker` in the same process - but it is the seam between an
upload returning and the indexing that follows it, so it gets an interface like
the other infra.

A backend must provide ordinary priority-queue semantics plus at-least-once
delivery with a dead-letter queue: `run_worker` re-queues a failed task up to
`max_retries` times and then moves it to `dlq_name`.
"""
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, Optional


# `async def process(payload: Dict[str, Any], operation_id: str) -> None`
# It must raise on failure - that is how the worker learns to retry.
ProcessFuncType = Callable[..., Awaitable[None]]


class MessageQueueService(ABC):
    """Abstract Base Class for a message queue backend."""

    @abstractmethod
    async def initialize(self) -> None:
        """Connects to the broker and verifies the connection."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Closes the connection."""
        pass

    @abstractmethod
    async def send_message(
        self,
        payload: Dict[str, Any],
        priority: float = 100.0,
        operation_id: Optional[str] = None,
    ) -> None:
        """Enqueues one task. Lower `priority` is consumed first."""
        pass

    @abstractmethod
    async def run_worker(
        self,
        process_func: ProcessFuncType,
        brpop_timeout: int = 5,
        error_retry_delay: int = 5,
        http_client: Optional[Any] = None,
    ) -> None:
        """Consumes until `stop_worker` is called. Retries and dead-letters failures."""
        pass

    @abstractmethod
    async def stop_worker(self) -> None:
        """Signals the consume loop to finish."""
        pass
