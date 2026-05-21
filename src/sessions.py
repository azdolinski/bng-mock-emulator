from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserProfile:
    username: str
    password: str
    radius_host: str
    radius_auth_port: int
    radius_acct_port: int
    radius_secret: str
    nas_ip: str
    auth_method: str = "chap"  # chap | pap
    accounting_enabled: bool = True
    accounting_interim_seconds: int = 0  # 0 = only Start/Stop
    extra_auth_attrs: dict[str, str] = field(default_factory=dict)
    extra_acct_attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class ActiveSession:
    username: str
    acct_session_id: str
    framed_ip: str | None = None
    started_at: str | None = None
    auth_reply_attrs: dict[str, str] = field(default_factory=dict)


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._users: dict[str, UserProfile] = {}
        self._active: dict[str, ActiveSession] = {}
        self.coa_default_response: str = "ack"  # ack | nak — for inactive or global override tests

    def upsert_user(self, profile: UserProfile) -> UserProfile:
        with self._lock:
            if profile.username in self._active:
                raise ValueError(f"user {profile.username!r} has an active session; stop it first")
            self._users[profile.username] = profile
            return profile

    def get_user(self, username: str) -> UserProfile | None:
        with self._lock:
            return self._users.get(username)

    def delete_user(self, username: str) -> bool:
        with self._lock:
            if username in self._active:
                raise ValueError(f"user {username!r} has an active session; stop it first")
            return self._users.pop(username, None) is not None

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for u, p in self._users.items():
                active = self._active.get(u)
                out.append(
                    {
                        "username": u,
                        "radius_host": p.radius_host,
                        "radius_auth_port": p.radius_auth_port,
                        "radius_acct_port": p.radius_acct_port,
                        "auth_method": p.auth_method,
                        "accounting_enabled": p.accounting_enabled,
                        "active": active is not None,
                        "acct_session_id": active.acct_session_id if active else None,
                    }
                )
            return out

    def start_session(self, username: str, *, acct_session_id: str | None = None, auth_reply_attrs: dict[str, str]) -> ActiveSession:
        with self._lock:
            if username not in self._users:
                raise KeyError(f"unknown user {username!r}")
            if username in self._active:
                raise ValueError(f"session already active for {username!r}")
            sid = acct_session_id or f"bng-{username}-{secrets.token_hex(4)}"
            from datetime import datetime, timezone

            sess = ActiveSession(
                username=username,
                acct_session_id=sid,
                started_at=datetime.now(timezone.utc).isoformat(),
                auth_reply_attrs=dict(auth_reply_attrs),
            )
            self._active[username] = sess
            return sess

    def stop_session(self, username: str) -> ActiveSession | None:
        with self._lock:
            return self._active.pop(username, None)

    def get_active(self, username: str) -> ActiveSession | None:
        with self._lock:
            return self._active.get(username)

    def is_active(self, username: str) -> bool:
        with self._lock:
            return username in self._active

    def active_usernames(self) -> set[str]:
        with self._lock:
            return set(self._active.keys())

    def match_coa_user(self, attrs: dict[str, str]) -> str | None:
        """Resolve User-Name from CoA/Disconnect packet."""
        uname = attrs.get("User-Name")
        if not uname:
            return None
        with self._lock:
            if uname in self._active:
                return uname
        return None
