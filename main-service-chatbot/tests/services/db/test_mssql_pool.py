"""The MSSQL connection pool never lends out a connection another thread is still using."""
import asyncio
import threading

import pytest
from app.services.db.mssql_service import MSSQLService


class FakeConn:
    def __init__(self, name):
        self.name = name
        self.closed = False

    def close(self):
        self.closed = True


def _service(*conns):
    service = MSSQLService("Server=x;UserID=u;Password=p;Database=d", 10, len(conns))
    service.pool = asyncio.Queue()
    for conn in conns:
        service.pool.put_nowait(conn)
    service._is_initialized = True
    fresh = iter(FakeConn(f"fresh-{i}") for i in range(10))
    service._connect = lambda: next(fresh)
    return service


@pytest.mark.asyncio
async def test_a_cancelled_call_does_not_return_its_connection_while_its_thread_still_runs(monkeypatch):
    original = FakeConn("original")
    service = _service(original)
    running, overlap = {}, []  # threads currently inside each connection
    release = threading.Event()
    used = []

    def fake_sync(conn, name, params):
        used.append(conn)
        running[conn.name] = running.get(conn.name, 0) + 1
        if running[conn.name] > 1:
            overlap.append(conn.name)
        release.wait(2)
        running[conn.name] -= 1
        return [[]]

    monkeypatch.setattr(MSSQLService, "_call_procedure_sync", staticmethod(fake_sync))
    first = asyncio.create_task(service.call_procedure("p1"))
    await asyncio.sleep(0.1)
    first.cancel()  # a client that hung up, or shutdown
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(service.call_procedure("p2"))
    await asyncio.sleep(0.2)
    release.set()
    await second

    assert not overlap
    assert used[0] is original and used[1] is not original  # the second call got a replacement
    assert original.closed
    assert service.pool.qsize() == 1  # and the pool did not shrink


@pytest.mark.asyncio
async def test_a_cancelled_call_keeps_the_pool_full_even_if_the_server_cannot_be_reached(monkeypatch):
    original = FakeConn("original")
    service = _service(original)
    release = threading.Event()

    def fake_sync(conn, name, params):
        release.wait(2)
        return [[]]

    def unreachable():
        raise OSError("connection refused")

    service._connect = unreachable
    monkeypatch.setattr(MSSQLService, "_call_procedure_sync", staticmethod(fake_sync))
    call = asyncio.create_task(service.call_procedure("p1"))
    await asyncio.sleep(0.1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    release.set()

    assert service.pool.qsize() == 1  # the (closed) connection holds the slot; the next call replaces it


@pytest.mark.asyncio
async def test_a_call_that_completes_returns_its_own_connection(monkeypatch):
    conn = FakeConn("original")
    service = _service(conn)
    monkeypatch.setattr(MSSQLService, "_call_procedure_sync", staticmethod(lambda c, n, p: [[{"id": 1}]]))

    assert await service.call_procedure("p") == [[{"id": 1}]]

    assert service.pool.get_nowait() is conn and not conn.closed
