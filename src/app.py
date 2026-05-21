from __future__ import annotations

from flask import Flask, jsonify, request

from .config import (
    API_HOST,
    API_PORT,
    COA_PORT,
    COA_SECRET,
    DEFAULT_NAS_IP,
    DEFAULT_RADIUS_SECRET,
)
from .coa_listener import CoaListener
from .logbook import LogBook
from .radius_io import RadiusClient
from .sessions import SessionStore, UserProfile

store = SessionStore()
log = LogBook()
radius = RadiusClient(store, log)
coa_listener = CoaListener(store, log)


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "coa_port": COA_PORT,
                "active_sessions": list(store.active_usernames()),
            }
        )

    @app.post("/users")
    def create_user():
        body = request.get_json(force=True, silent=True) or {}
        username = body.get("username")
        password = body.get("password")
        if not username or not password:
            return jsonify({"error": "username and password are required"}), 400

        profile = UserProfile(
            username=username,
            password=password,
            radius_host=body.get("radius_host", "127.0.0.1"),
            radius_auth_port=int(body.get("radius_auth_port", 2812)),
            radius_acct_port=int(body.get("radius_acct_port", 2813)),
            radius_secret=body.get("radius_secret", DEFAULT_RADIUS_SECRET),
            nas_ip=body.get("nas_ip", DEFAULT_NAS_IP),
            auth_method=body.get("auth_method", "chap"),
            accounting_enabled=bool(body.get("accounting_enabled", True)),
            accounting_interim_seconds=int(body.get("accounting_interim_seconds", 0)),
            extra_auth_attrs=body.get("extra_auth_attrs") or {},
            extra_acct_attrs=body.get("extra_acct_attrs") or {},
        )
        try:
            store.upsert_user(profile)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        log.append(
            direction="outbound",
            exchange="api",
            packet="POST /users",
            username=username,
            note="user profile created",
        )
        return jsonify({"username": username, "created": True}), 201

    @app.get("/users")
    def list_users():
        return jsonify({"users": store.list_users()})

    @app.get("/users/<username>")
    def get_user(username: str):
        profile = store.get_user(username)
        if not profile:
            return jsonify({"error": "not found"}), 404
        active = store.get_active(username)
        return jsonify(
            {
                "username": profile.username,
                "radius_host": profile.radius_host,
                "radius_auth_port": profile.radius_auth_port,
                "radius_acct_port": profile.radius_acct_port,
                "nas_ip": profile.nas_ip,
                "auth_method": profile.auth_method,
                "accounting_enabled": profile.accounting_enabled,
                "accounting_interim_seconds": profile.accounting_interim_seconds,
                "active": active is not None,
                "session": {
                    "acct_session_id": active.acct_session_id,
                    "started_at": active.started_at,
                }
                if active
                else None,
            }
        )

    @app.delete("/users/<username>")
    def delete_user(username: str):
        try:
            ok = store.delete_user(username)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"username": username, "deleted": True})

    @app.post("/users/<username>/start")
    def start_session(username: str):
        profile = store.get_user(username)
        if not profile:
            return jsonify({"error": "user not found; POST /users first"}), 404

        try:
            rtype, reply_attrs = radius.authenticate(profile)
        except Exception as exc:
            log.append(
                direction="outbound",
                exchange="auth",
                packet="(error)",
                username=username,
                note=str(exc),
            )
            return jsonify({"error": f"auth failed: {exc}"}), 502

        if rtype != "Access-Accept":
            return jsonify(
                {
                    "username": username,
                    "started": False,
                    "auth_result": rtype,
                    "reply": reply_attrs,
                }
            ), 422

        try:
            session = store.start_session(username, auth_reply_attrs=reply_attrs)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409

        if profile.accounting_enabled:
            try:
                radius.acct(profile, session, "Start")
            except Exception as exc:
                store.stop_session(username)
                return jsonify({"error": f"acct Start failed: {exc}"}), 502
            radius.start_accounting_loop(profile, session)

        log.append(
            direction="outbound",
            exchange="api",
            packet="POST /users/.../start",
            username=username,
            note="session registered",
            attributes={"acct_session_id": session.acct_session_id},
        )
        return jsonify(
            {
                "username": username,
                "started": True,
                "auth_result": rtype,
                "acct_session_id": session.acct_session_id,
                "reply": reply_attrs,
            }
        )

    @app.post("/users/<username>/stop")
    def stop_session(username: str):
        profile = store.get_user(username)
        if not profile:
            return jsonify({"error": "not found"}), 404
        session = store.get_active(username)
        if not session:
            return jsonify({"error": "no active session"}), 409

        radius.stop_accounting_loop(username)
        if profile.accounting_enabled:
            try:
                radius.acct(profile, session, "Stop")
            except Exception as exc:
                return jsonify({"error": f"acct Stop failed: {exc}"}), 502
        store.stop_session(username)
        log.append(
            direction="outbound",
            exchange="api",
            packet="POST /users/.../stop",
            username=username,
            note="session stopped",
        )
        return jsonify({"username": username, "stopped": True})

    @app.get("/logs")
    def get_logs():
        username = request.args.get("username")
        limit = request.args.get("limit", type=int)
        entries = log.list(username=username, limit=limit)
        return jsonify({"total": len(entries), "entries": entries})

    @app.delete("/logs")
    def clear_logs():
        n = log.clear()
        return jsonify({"cleared": n})

    @app.put("/config/coa-response")
    def set_coa_response():
        body = request.get_json(force=True, silent=True) or {}
        mode = (body.get("mode") or "").lower()
        if mode not in ("ack", "nak"):
            return jsonify({"error": "mode must be 'ack' or 'nak'"}), 400
        store.coa_default_response = mode
        return jsonify({"coa_default_response": mode})

    return app


def main() -> None:
    coa_listener.start()
    app = create_app()
    print(f"BNG mock emulator: API http://{API_HOST}:{API_PORT}  CoA UDP :{COA_PORT}  secret={COA_SECRET!r}")
    app.run(host=API_HOST, port=API_PORT, threaded=True)


if __name__ == "__main__":
    main()
