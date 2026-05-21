from __future__ import annotations

import itertools
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _attrs_to_dict(attrs: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in attrs.items():
        if isinstance(v, bytes):
            try:
                out[k] = v.decode("utf-8")
            except UnicodeDecodeError:
                out[k] = "0x" + v.hex()
        else:
            out[k] = str(v)
    return out


@dataclass
class LogEntry:
    id: int
    ts: str
    direction: str  # outbound | inbound
    exchange: str  # auth | acct | coa | disconnect | api
    username: str | None
    packet: str
    attributes: dict[str, str] = field(default_factory=dict)
    peer: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LogBook:
    """Thread-safe append-only RADIUS / API exchange log."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._entries: list[LogEntry] = []

    def append(
        self,
        *,
        direction: str,
        exchange: str,
        packet: str,
        username: str | None = None,
        attributes: dict[str, Any] | None = None,
        peer: str | None = None,
        note: str | None = None,
    ) -> LogEntry:
        entry = LogEntry(
            id=next(self._seq),
            ts=_utc_now(),
            direction=direction,
            exchange=exchange,
            username=username,
            packet=packet,
            attributes=_attrs_to_dict(attributes or {}),
            peer=peer,
            note=note,
        )
        with self._lock:
            self._entries.append(entry)
        return entry

    def list(self, *, username: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._entries)
        if username:
            rows = [e for e in rows if e.username == username]
        if limit is not None and limit > 0:
            rows = rows[-limit:]
        return [e.to_dict() for e in rows]

    def clear(self) -> int:
        with self._lock:
            n = len(self._entries)
            self._entries.clear()
        return n
