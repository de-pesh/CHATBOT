"""An in-memory stand-in for the slice of redis.asyncio.Redis the queue uses, shared by the queue tests.

Sorted sets are real (member -> score), so leases can be inspected and raced. `script` is what
successive BZPOPMIN calls return (a tuple, None, or an exception to raise); when it runs out
the service's shutdown event is set and the worker loop ends.
"""
import asyncio
import json

import redis.exceptions as redis_exceptions

LEASES = "q:inflight"


class FakeRedis:
    def __init__(self, service, script, zadd_error=None):
        self.service = service
        self.script = list(script)
        self.zadd_error = zadd_error
        self.sent = []  # (queue, decoded member, score) for every ZADD to a queue - not to the lease set
        self.zadd_calls = 0
        self.zsets: dict[str, dict[str, float]] = {}

    @property
    def leases(self) -> dict[str, float]:
        return self.zsets.get(LEASES, {})

    async def bzpopmin(self, queue, timeout):
        if not self.script:
            self.service._shutdown_event.set()
            return None
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def zadd(self, queue, mapping):
        self.zadd_calls += 1
        if callable(self.zadd_error) and self.zadd_error(queue):
            raise redis_exceptions.ConnectionError("zadd failed")
        for member, score in mapping.items():
            self.zsets.setdefault(queue, {})[member] = score
            if queue != LEASES:
                self.sent.append((queue, json.loads(member), score))

    async def zrem(self, queue, member):
        return 1 if self.zsets.get(queue, {}).pop(member, None) is not None else 0

    async def zrange(self, queue, start, end, withscores=False):
        members = sorted(self.zsets.get(queue, {}).items(), key=lambda item: item[1])
        await asyncio.sleep(0)  # a point where a concurrent reaper can interleave
        return members if withscores else [member for member, _ in members]
