"""Always-on diagnostics file, so a non-technical user never has to capture anything.

The people using this connector are recruiters. Asking them to reproduce a failure
with logging turned up, or to copy a raw HTTP response out of a console, is not a
workable support path — so every failure and every oversized result is appended to
one JSON-lines file at a fixed, predictable location. Support then becomes "send me
this file", or the maintainer reads it directly.

Deliberately independent of `logging.py`: that writer is gated by
GREENHOUSE_LOG_LEVEL (default "warning") and writes to stderr, whose capture depends
on the host client. Diagnostics must survive both of those, so it always writes, at
its own path, regardless of log level.

Written to a user-local file only — never returned to the model and never sent
anywhere. Auth headers are never recorded, and URLs are reduced to masked endpoints
before they reach here.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX_BYTES = 2_000_000  # rotate once past ~2MB; keeps one previous generation
_MAX_DETAIL_CHARS = 2_000


def _default_path() -> Path:
    """Sit next to Claude's own logs on macOS so it is easy to describe and find."""
    mac_logs = Path.home() / "Library" / "Logs" / "Claude"
    if mac_logs.is_dir():
        return mac_logs / "greenhouse-mcp-diagnostics.jsonl"
    return Path.home() / ".open-greenhouse-mcp" / "diagnostics.jsonl"


def diagnostics_path() -> Path | None:
    """Resolved diagnostics path, or None when explicitly disabled."""
    if os.environ.get("GREENHOUSE_DIAGNOSTICS", "").lower() in ("off", "0", "false", "no"):
        return None
    override = os.environ.get("GREENHOUSE_DIAGNOSTICS_FILE")
    return Path(override).expanduser() if override else _default_path()


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def _clip(value: Any) -> Any:
    """Keep detail readable and bounded without losing the useful head of it."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= _MAX_DETAIL_CHARS:
        return value
    return text[:_MAX_DETAIL_CHARS] + f"… [clipped, {len(text)} chars total]"


def record(event: str, **fields: Any) -> None:
    """Append one diagnostic record. Never raises, never blocks a tool call."""
    path = diagnostics_path()
    if path is None:
        return
    try:
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": event,
            **{k: _clip(v) for k, v in fields.items() if v is not None},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except Exception:  # pragma: no cover — diagnostics must never break a tool call
        pass
