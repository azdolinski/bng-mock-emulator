from __future__ import annotations

import hashlib
import secrets
import threading
from typing import TYPE_CHECKING, Any

from pyrad import packet
from pyrad.client import Client
from pyrad.dictionary import Dictionary

from .config import DICTIONARY
from .logbook import LogBook
from .sessions import SessionStore, UserProfile

if TYPE_CHECKING:
    from .sessions import ActiveSession

_ACCT_STATUS_TYPE = {
    "Start": 1,
    "Stop": 2,
    "Interim-Update": 3,
    "Accounting-On": 7,
    "Accounting-Off": 8,
}

_RAD_NAMES = {
    packet.AccessRequest: "Access-Request",
    packet.AccessAccept: "Access-Accept",
    packet.AccessReject: "Access-Reject",
    packet.AccountingRequest: "Accounting-Request",
    packet.AccountingResponse: "Accounting-Response",
}


def _packet_attrs(req_or_reply) -> dict[str, Any]:
    return {k: req_or_reply[k][0] for k in req_or_reply.keys()}


def _rtype_name(code: int) -> str:
    return _RAD_NAMES.get(code, f"code={code}")


class RadiusClient:
    def __init__(self, store: SessionStore, log: LogBook) -> None:
        self._store = store
        self._log = log
        self._dict = Dictionary(DICTIONARY)
        self._acct_threads: dict[str, threading.Thread] = {}
        self._acct_stop: dict[str, threading.Event] = {}

    def _client(self, profile: UserProfile, *, acct: bool = False) -> Client:
        c = Client(
            server=profile.radius_host,
            secret=profile.radius_secret.encode(),
            dict=self._dict,
        )
        if acct:
            c.acctport = profile.radius_acct_port
        else:
            c.authport = profile.radius_auth_port
        return c

    def authenticate(self, profile: UserProfile) -> tuple[str, dict[str, str]]:
        if profile.auth_method == "pap":
            return self._auth_pap(profile)
        if profile.auth_method == "chap":
            return self._auth_chap(profile)
        raise ValueError(f"unsupported auth_method {profile.auth_method!r}")

    def _auth_pap(self, profile: UserProfile) -> tuple[str, dict[str, str]]:
        c = self._client(profile)
        req = c.CreateAuthPacket(code=packet.AccessRequest)
        req["User-Name"] = profile.username
        req["User-Password"] = profile.password
        req["Service-Type"] = "Framed"
        req["Framed-Protocol"] = "PPP"
        req["NAS-IP-Address"] = profile.nas_ip
        for k, v in profile.extra_auth_attrs.items():
            req[k] = v
        try:
            req.add_message_authenticator()
        except AttributeError:
            pass
        attrs = _packet_attrs(req)
        self._log.append(
            direction="outbound",
            exchange="auth",
            packet="Access-Request",
            username=profile.username,
            attributes=attrs,
            peer=f"{profile.radius_host}:{profile.radius_auth_port}",
        )
        reply = c.SendPacket(req)
        rtype = _rtype_name(reply.code)
        reply_attrs = _packet_attrs(reply)
        self._log.append(
            direction="inbound",
            exchange="auth",
            packet=rtype,
            username=profile.username,
            attributes=reply_attrs,
            peer=f"{profile.radius_host}:{profile.radius_auth_port}",
        )
        return rtype, {k: str(v) if not isinstance(v, bytes) else v.decode(errors="replace") for k, v in reply_attrs.items()}

    def _auth_chap(self, profile: UserProfile) -> tuple[str, dict[str, str]]:
        c = self._client(profile)
        req = c.CreateAuthPacket(code=packet.AccessRequest)
        chap_challenge = secrets.token_bytes(16)
        chap_id = secrets.randbelow(256)
        md5_in = bytes([chap_id]) + profile.password.encode() + chap_challenge
        req["User-Name"] = profile.username
        req["CHAP-Challenge"] = chap_challenge
        req["CHAP-Password"] = bytes([chap_id]) + hashlib.md5(md5_in).digest()
        req["Service-Type"] = "Framed"
        req["Framed-Protocol"] = "PPP"
        req["NAS-IP-Address"] = profile.nas_ip
        for k, v in profile.extra_auth_attrs.items():
            req[k] = v
        try:
            req.add_message_authenticator()
        except AttributeError:
            pass
        attrs = _packet_attrs(req)
        self._log.append(
            direction="outbound",
            exchange="auth",
            packet="Access-Request",
            username=profile.username,
            attributes=attrs,
            peer=f"{profile.radius_host}:{profile.radius_auth_port}",
        )
        reply = c.SendPacket(req)
        rtype = _rtype_name(reply.code)
        reply_attrs = _packet_attrs(reply)
        self._log.append(
            direction="inbound",
            exchange="auth",
            packet=rtype,
            username=profile.username,
            attributes=reply_attrs,
            peer=f"{profile.radius_host}:{profile.radius_auth_port}",
        )
        return rtype, {k: str(v) if not isinstance(v, bytes) else v.decode(errors="replace") for k, v in reply_attrs.items()}

    def acct(self, profile: UserProfile, session: ActiveSession, status: str) -> None:
        c = self._client(profile, acct=True)
        req = c.CreateAcctPacket(code=packet.AccountingRequest)
        req["User-Name"] = profile.username
        req["Acct-Status-Type"] = _ACCT_STATUS_TYPE.get(status, status)
        req["Acct-Session-Id"] = session.acct_session_id
        req["Service-Type"] = "Framed"
        req["Framed-Protocol"] = "PPP"
        req["NAS-IP-Address"] = profile.nas_ip
        if session.framed_ip:
            req["Framed-IP-Address"] = session.framed_ip
        for k, v in profile.extra_acct_attrs.items():
            req[k] = v
        attrs = _packet_attrs(req)
        self._log.append(
            direction="outbound",
            exchange="acct",
            packet="Accounting-Request",
            username=profile.username,
            attributes=attrs,
            peer=f"{profile.radius_host}:{profile.radius_acct_port}",
            note=f"Acct-Status-Type={status}",
        )
        reply = c.SendPacket(req)
        reply_attrs = _packet_attrs(reply)
        self._log.append(
            direction="inbound",
            exchange="acct",
            packet=_rtype_name(reply.code),
            username=profile.username,
            attributes=reply_attrs,
            peer=f"{profile.radius_host}:{profile.radius_acct_port}",
        )

    def start_accounting_loop(self, profile: UserProfile, session: ActiveSession) -> None:
        if not profile.accounting_enabled:
            return
        interval = profile.accounting_interim_seconds
        if interval <= 0:
            return
        stop = threading.Event()
        self._acct_stop[profile.username] = stop

        def _loop() -> None:
            while not stop.wait(interval):
                try:
                    if self._store.is_active(profile.username):
                        self.acct(profile, session, "Interim-Update")
                except Exception:
                    break

        t = threading.Thread(target=_loop, name=f"acct-{profile.username}", daemon=True)
        self._acct_threads[profile.username] = t
        t.start()

    def stop_accounting_loop(self, username: str) -> None:
        ev = self._acct_stop.pop(username, None)
        if ev:
            ev.set()
        self._acct_threads.pop(username, None)
