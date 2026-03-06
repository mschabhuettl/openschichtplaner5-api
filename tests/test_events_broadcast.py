"""Tests for api/routers/events.py — broadcast() + _event_generator()."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# broadcast()
# ---------------------------------------------------------------------------


class TestBroadcast:
    def setup_method(self):
        """Clear all SSE subscribers before each test."""
        from api.routers import events as ev
        with ev._lock:
            ev._subscribers.clear()

    def test_broadcast_no_subscribers_no_error(self):
        """broadcast() with zero subscribers must not raise."""
        from api.routers.events import broadcast
        broadcast("schedule_changed", {"month": "2024-01"})

    def test_broadcast_delivers_to_queue(self):
        """broadcast() puts a payload into a subscriber queue."""
        from api.routers import events as ev

        loop = asyncio.new_event_loop()
        try:
            q = asyncio.Queue()
            with ev._lock:
                ev._subscribers.append((loop, q))

            ev.broadcast("conflict_updated", {"id": 42})

            # drain the queue via run_until_complete
            async def _get():
                return await asyncio.wait_for(q.get(), timeout=1.0)

            payload = loop.run_until_complete(_get())
            assert payload["type"] == "conflict_updated"
            assert payload["data"] == {"id": 42}
        finally:
            loop.close()

    def test_broadcast_removes_dead_subscriber(self):
        """Dead subscribers (closed loop) are cleaned up after broadcast."""
        from api.routers import events as ev

        dead_loop = MagicMock()
        dead_loop.call_soon_threadsafe = MagicMock(side_effect=RuntimeError("dead"))
        dead_q = asyncio.Queue()

        with ev._lock:
            ev._subscribers.append((dead_loop, dead_q))

        before = len(ev._subscribers)
        ev.broadcast("test_event")
        after = len(ev._subscribers)
        assert after < before  # dead entry removed

    def test_broadcast_delivers_to_multiple_subscribers(self):
        """broadcast() reaches all live subscribers."""
        from api.routers import events as ev

        loop = asyncio.new_event_loop()
        try:
            q1 = asyncio.Queue()
            q2 = asyncio.Queue()
            with ev._lock:
                ev._subscribers.append((loop, q1))
                ev._subscribers.append((loop, q2))

            ev.broadcast("note_added", {"note": "hello"})

            async def _drain():
                p1 = await asyncio.wait_for(q1.get(), timeout=1.0)
                p2 = await asyncio.wait_for(q2.get(), timeout=1.0)
                return p1, p2

            p1, p2 = loop.run_until_complete(_drain())
            assert p1["type"] == "note_added"
            assert p2["type"] == "note_added"
        finally:
            loop.close()

    def test_broadcast_default_data_is_empty_dict(self):
        """broadcast() without data= arg defaults to {}."""
        from api.routers import events as ev

        loop = asyncio.new_event_loop()
        try:
            q = asyncio.Queue()
            with ev._lock:
                ev._subscribers.append((loop, q))

            ev.broadcast("absence_changed")

            async def _get():
                return await asyncio.wait_for(q.get(), timeout=1.0)

            payload = loop.run_until_complete(_get())
            assert payload["data"] == {}
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# _event_generator()
# ---------------------------------------------------------------------------


class TestEventGenerator:
    @pytest.mark.asyncio
    async def test_sends_connected_event_first(self):
        """Generator must yield 'connected' event immediately."""
        from api.routers.events import _event_generator

        request = MagicMock()
        request.is_disconnected = AsyncMock(side_effect=[False, True])
        queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        gen = _event_generator(request, queue, loop)
        first = await gen.__anext__()
        assert "event: connected" in first

    @pytest.mark.asyncio
    async def test_yields_queued_event(self):
        """Generator yields SSE-formatted event from queue."""
        from api.routers.events import _event_generator

        request = MagicMock()
        # First: not disconnected (so we process one queue item), then disconnected
        request.is_disconnected = AsyncMock(side_effect=[False, False, True])
        queue = asyncio.Queue()
        await queue.put({"type": "schedule_changed", "data": {"foo": "bar"}})
        loop = asyncio.get_event_loop()

        gen = _event_generator(request, queue, loop)
        connected_msg = await gen.__anext__()  # "event: connected\ndata: {}\n\n"
        assert "connected" in connected_msg
        event_msg = await gen.__anext__()
        assert "event: schedule_changed" in event_msg
        assert '"foo": "bar"' in event_msg

    @pytest.mark.asyncio
    async def test_sends_keepalive_on_timeout(self):
        """Generator yields keepalive comment when queue is empty (timeout)."""
        from api.routers.events import _event_generator

        request = MagicMock()
        # First iteration: not disconnected (will timeout), then disconnected
        request.is_disconnected = AsyncMock(side_effect=[False, False, True])
        queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        gen = _event_generator(request, queue, loop)
        _ = await gen.__anext__()  # connected

        async def _fast_timeout(coro, timeout):
            raise asyncio.TimeoutError()

        with patch("asyncio.wait_for", side_effect=_fast_timeout):
            keepalive = await gen.__anext__()
        assert "keepalive" in keepalive

    @pytest.mark.asyncio
    async def test_cleanup_removes_subscriber(self):
        """Generator removes its (loop, queue) pair from _subscribers on exit."""
        from api.routers import events as ev
        from api.routers.events import _event_generator

        with ev._lock:
            ev._subscribers.clear()

        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=True)
        queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        with ev._lock:
            ev._subscribers.append((loop, queue))

        gen = _event_generator(request, queue, loop)
        try:
            _ = await gen.__anext__()  # connected event
            await gen.__anext__()  # disconnected → StopAsyncIteration
        except StopAsyncIteration:
            pass

        with ev._lock:
            assert (loop, queue) not in ev._subscribers
