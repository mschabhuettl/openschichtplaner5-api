"""Rate-limit event storage — logs 429 events to a JSON-lines file.

Each event is appended as a single JSON line for fast writes and easy parsing.
The store supports querying by time range and user, with automatic rotation
to keep the file under 10 000 entries.
"""

import json
import os
import threading
from datetime import UTC, datetime

_RATE_LIMIT_LOG = os.environ.get(
    "SP5_RATE_LIMIT_LOG",
    os.path.join(os.path.dirname(__file__), "..", "data", "rate_limit_events.jsonl"),
)
_MAX_EVENTS = 10_000
_lock = threading.Lock()


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(_RATE_LIMIT_LOG), exist_ok=True)


def log_rate_limit_event(
    *,
    user: str | None,
    ip: str,
    endpoint: str,
    detail: str = "",
) -> None:
    """Append a rate-limit event to the log file."""
    entry = {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "user": user or "",
        "ip": ip,
        "endpoint": endpoint,
        "detail": detail,
    }
    try:
        _ensure_dir()
        with _lock:
            with open(_RATE_LIMIT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never crash the request path


def get_rate_limit_events(
    *,
    since: str | None = None,
    until: str | None = None,
    user: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Read rate-limit events, optionally filtered by time range and user.

    Returns newest-first, capped at *limit* entries.
    """
    _ensure_dir()
    if not os.path.exists(_RATE_LIMIT_LOG):
        return []

    events: list[dict] = []
    try:
        with _lock:
            with open(_RATE_LIMIT_LOG, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # Filter by time range
                    ts = evt.get("timestamp", "")
                    if since and ts < since:
                        continue
                    if until and ts > until:
                        continue
                    # Filter by user
                    if user and evt.get("user", "") != user:
                        continue
                    events.append(evt)
    except Exception:
        return []

    # newest first, capped
    events.reverse()
    return events[:limit]


def rotate_events() -> int:
    """Trim the log file to the newest _MAX_EVENTS entries.

    Returns the number of removed entries.
    """
    _ensure_dir()
    if not os.path.exists(_RATE_LIMIT_LOG):
        return 0

    try:
        with _lock:
            with open(_RATE_LIMIT_LOG, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= _MAX_EVENTS:
                return 0
            removed = len(lines) - _MAX_EVENTS
            with open(_RATE_LIMIT_LOG, "w", encoding="utf-8") as f:
                f.writelines(lines[-_MAX_EVENTS:])
            return removed
    except Exception:
        return 0
