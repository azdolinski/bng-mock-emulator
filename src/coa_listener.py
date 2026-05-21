from __future__ import annotations

import socket
import struct
import threading
from typing import TYPE_CHECKING

from pyrad import packet
from pyrad.dictionary import Dictionary

from .config import COA_HOST, COA_PORT, COA_SECRET, DICTIONARY
from .logbook import LogBook

if TYPE_CHECKING:
    from .sessions import SessionStore

_COA_NAMES = {
    packet.CoARequest: "CoA-Request",
    packet.DisconnectRequest: "Disconnect-Request",
    packet.CoAACK: "CoA-ACK",
    packet.CoANAK: "CoA-NAK",
    packet.DisconnectACK: "Disconnect-ACK",
    packet.DisconnectNAK: "Disconnect-NAK",
}


def _normalize_radius_udp(data: bytes) -> bytes:
    """Trim trailing bytes when UDP length > RADIUS Length (FreeRADIUS coa_nas_relay)."""
    if len(data) < 4:
        return data
    declared = struct.unpack("!H", data[2:4])[0]
    if 20 <= declared < len(data):
        return data[:declared]
    return data


def _attrs_dict(req) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in req.keys():
        v = req[k][0]
        if isinstance(v, bytes):
            try:
                out[k] = v.decode("utf-8")
            except UnicodeDecodeError:
                out[k] = "0x" + v.hex()
        else:
            out[k] = str(v)
    return out


class CoaListener:
    """UDP listener for CoA-Request and Disconnect-Request (BNG side)."""

    def __init__(self, store: SessionStore, log: LogBook) -> None:
        self._store = store
        self._log = log
        self._secret = COA_SECRET.encode()
        self._dict: Dictionary | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    def start(self) -> None:
        self._dict = Dictionary(DICTIONARY)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="coa-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((COA_HOST, COA_PORT))
        sock.settimeout(0.5)
        self._sock = sock
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            self._handle(sock, data, addr)

    def _handle(self, sock: socket.socket, data: bytes, addr: tuple[str, int]) -> None:
        raw_len = len(data)
        data = _normalize_radius_udp(data)
        try:
            req = packet.Packet(secret=self._secret, dict=self._dict, packet=data)
        except Exception as exc:
            self._log.append(
                direction="inbound",
                exchange="coa",
                packet="(parse-error)",
                peer=f"{addr[0]}:{addr[1]}",
                note=f"{exc}; udp={raw_len} radius={len(data)} head={data[:20].hex()}",
            )
            return

        attrs = _attrs_dict(req)
        pkt_name = _COA_NAMES.get(req.code, f"code={req.code}")
        username = attrs.get("User-Name")

        self._log.append(
            direction="inbound",
            exchange="coa" if req.code == packet.CoARequest else "disconnect",
            packet=pkt_name,
            username=username,
            attributes=attrs,
            peer=f"{addr[0]}:{addr[1]}",
        )

        active_user = self._store.match_coa_user(attrs) if username else None
        use_ack = active_user is not None and self._store.coa_default_response == "ack"

        if req.code == packet.CoARequest:
            reply_code = packet.CoAACK if use_ack else packet.CoANAK
            exchange = "coa"
        elif req.code == packet.DisconnectRequest:
            reply_code = packet.DisconnectACK if use_ack else packet.DisconnectNAK
            exchange = "disconnect"
            if use_ack and active_user:
                self._store.stop_session(active_user)
        else:
            return

        rep = req.CreateReply(code=reply_code)
        sock.sendto(rep.ReplyPacket(), addr)
        reply_name = _COA_NAMES.get(reply_code, f"code={reply_code}")

        self._log.append(
            direction="outbound",
            exchange=exchange,
            packet=reply_name,
            username=username,
            attributes=_attrs_dict(rep),
            peer=f"{addr[0]}:{addr[1]}",
            note="active session" if use_ack else "no active session or nak mode",
        )
