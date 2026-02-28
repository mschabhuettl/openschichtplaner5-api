"""Server-Sent Events (SSE) router for real-time updates."""
import asyncio
import json
import logging
import threading
from typing import AsyncGenerator
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["Events"])

# ── In-memory subscriber registry ──────────────────────────────
# List of (loop, queue) tuples — each SSE connection gets one.
_lock = threading.Lock()
_subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []


def broadcast(event_type: str, data: dict | None = None) -> None:
    """Broadcast an event to all connected SSE clients.

    Thread-safe — can be called from sync FastAPI endpoints.
    """
    payload = {"type": event_type, "data": data or {}}
    with _lock:
        dead = []
        for loop, q in _subscribers:
            try:
                loop.call_soon_threadsafe(q.put_nowait, payload)
            except Exception:
                dead.append((loop, q))
        for item in dead:
            try:
                _subscribers.remove(item)
            except ValueError:
                pass
    if _subscribers:
        _logger.debug("SSE broadcast: %s → %d clients", event_type, len(_subscribers))


async def _event_generator(request: Request, queue: asyncio.Queue) -> AsyncGenerator[str, None]:
    """Yield SSE-formatted strings from the queue until the client disconnects."""
    try:
        # Send initial "connected" event
        yield f"event: connected\ndata: {{}}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                event_type = payload["type"]
                data_str = json.dumps(payload["data"])
                yield f"event: {event_type}\ndata: {data_str}\n\n"
            except asyncio.TimeoutError:
                # Send keepalive comment every 25s
                yield ": keepalive\n\n"
    finally:
        loop = asyncio.get_event_loop()
        with _lock:
            try:
                _subscribers.remove((loop, queue))
            except ValueError:
                pass
        _logger.debug("SSE client disconnected. Remaining: %d", len(_subscribers))


@router.get("", summary="SSE event stream", description=(
    "Connect to receive real-time events.\n\n"
    "Events: `connected`, `schedule_changed`, `conflict_updated`, `note_added`, `absence_changed`"
))
async def sse_stream(request: Request):
    """SSE endpoint — stream real-time events to connected clients."""
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    with _lock:
        _subscribers.append((loop, queue))
    _logger.debug("SSE client connected. Total: %d", len(_subscribers))

    return StreamingResponse(
        _event_generator(request, queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
