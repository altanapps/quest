"""Revocation after login must reach cookies, live sockets and sandbox leases."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

import auth.session as session
from auth.config import COOKIE_NAME
from chat.realtime import socket as realtime


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def identity(monkeypatch):
    user = {"id": 7, "email": "former@external.test", "session_epoch": 3}
    allowed = [user["email"]]
    monkeypatch.setenv("QUEST_ENV", "prod")
    monkeypatch.setattr("auth.config.allowed_login_domain", lambda: "approved.test")
    monkeypatch.setattr("auth.config.allowed_login_emails", lambda: list(allowed))
    monkeypatch.setattr(session, "get_secret_key", lambda: "synthetic-test-secret")
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(session, "get_user_by_id", lookup)
    monkeypatch.setattr(realtime, "get_user_by_id", lookup)
    monkeypatch.setattr("db.user_store.get_user_by_id", lookup)
    response = session.set_session_cookie(JSONResponse({}), user)
    from http.cookies import SimpleCookie
    jar = SimpleCookie()
    jar.load(response.headers["set-cookie"])
    return SimpleNamespace(user=user, allowed=allowed, cookie=jar[COOKIE_NAME].value, lookup=lookup)


def _request(cookie):
    return Request({
        "type": "http", "method": "POST", "path": "/",
        "headers": [(b"cookie", f"{COOKIE_NAME}={cookie}".encode())],
        "scheme": "https", "server": ("audit.test", 443), "query_string": b"",
    })


def test_valid_cookie_is_denied_after_policy_change(identity):
    assert _run(session.get_user_from_cookie(identity.cookie))["id"] == 7
    identity.allowed.clear()
    with pytest.raises(HTTPException) as exc:
        _run(session.get_user_from_cookie(identity.cookie))
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "access_denied"


def test_manual_cookie_route_cannot_update_credentials_after_revocation(monkeypatch, identity):
    import auth.airtable as airtable
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(airtable, "update_user_field", update)
    app = FastAPI()
    app.include_router(airtable.router)
    identity.allowed.clear()

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, identity.cookie)
        response = client.post("/auth/airtable/save-token", json={"token": "patSynthetic"})

    assert response.status_code == 403
    update.assert_not_awaited()


def test_sign_out_still_revokes_an_offboarded_session(monkeypatch, identity):
    bump = AsyncMock()
    monkeypatch.setattr(session, "bump_session_epoch", bump)
    identity.allowed.clear()
    _run(session.revoke_cookie_session(_request(identity.cookie)))
    bump.assert_awaited_once_with(7)


@pytest.mark.parametrize("admitted", [False, True])
def test_socket_heartbeat_rechecks_admission(monkeypatch, identity, admitted):
    ws = SimpleNamespace(cookies={COOKIE_NAME: identity.cookie})
    conn = realtime._Connection(ws, identity.user)
    if not admitted:
        identity.allowed.clear()
    assert _run(realtime._revalidate_auth(conn)) is admitted


@pytest.mark.parametrize("op", ["subscribe", "send_message", "stop"])
@pytest.mark.parametrize("admitted", [False, True])
def test_control_frame_checks_fresh_admission_before_dispatch(monkeypatch, identity, op, admitted):
    ws = SimpleNamespace(
        receive_json=AsyncMock(side_effect=[{"op": op, "conversation_id": "owned"}, WebSocketDisconnect()]),
        close=AsyncMock(),
    )
    conn = realtime._Connection(ws, identity.user)
    handler = AsyncMock()
    monkeypatch.setattr(realtime, "_handle_" + op, handler)
    monkeypatch.setattr(realtime, "_send_to_client", AsyncMock())
    if not admitted:
        identity.allowed.clear()

    _run(realtime._reader_loop(conn))

    assert handler.await_count == int(admitted)
    if admitted:
        ws.close.assert_not_awaited()
    else:
        ws.close.assert_awaited_once_with(code=realtime.CLOSE_AUTH_FAILED)


def test_control_frame_uses_fresh_email_instead_of_connect_time_identity(monkeypatch, identity):
    conn = realtime._Connection(SimpleNamespace(), dict(identity.user))
    identity.lookup.return_value = {**identity.user, "email": "different@external.test"}
    assert _run(realtime._admission_is_current(conn)) is False


def test_control_frame_fails_closed_when_admission_cannot_be_loaded(monkeypatch, identity):
    conn = realtime._Connection(SimpleNamespace(), identity.user)
    identity.lookup.side_effect = RuntimeError("database unavailable")
    assert _run(realtime._admission_is_current(conn)) is False


def test_live_sandbox_lease_does_not_bypass_offboarding(identity):
    from chat.sandbox_tokens import get_current_sandbox_user, sandbox_token_lease

    with sandbox_token_lease(7, ttl_seconds=60) as token:
        request = SimpleNamespace(
            headers={"Authorization": "Bearer " + token}, state=SimpleNamespace(),
        )
        assert _run(get_current_sandbox_user(request))["id"] == 7
        identity.allowed.clear()
        with pytest.raises(HTTPException) as exc:
            _run(get_current_sandbox_user(request))
        assert exc.value.status_code == 403


def test_admitted_admin_can_exit_impersonation_of_offboarded_target(monkeypatch, identity):
    import chat.routes.admin as admin
    from http.cookies import SimpleCookie

    owner = {"id": 8, "email": "admin@approved.test", "session_epoch": 1}
    users = {7: identity.user, 8: owner}
    lookup = AsyncMock(side_effect=users.get)
    monkeypatch.setattr(session, "get_user_by_id", lookup)
    monkeypatch.setattr(admin, "get_user_by_id", lookup)
    monkeypatch.setattr("chat.auth.is_admin", lambda email: email == owner["email"])
    monkeypatch.setattr(admin, "is_password_login", lambda: False)
    response = session.set_impersonation_cookie(JSONResponse({}), identity.user, owner)
    jar = SimpleCookie()
    jar.load(response.headers["set-cookie"])
    cookie = jar[COOKIE_NAME].value
    identity.allowed.clear()

    with pytest.raises(HTTPException):
        _run(session.get_user_from_cookie(cookie))
    restored = _run(admin.admin_stop_impersonation(_request(cookie)))
    jar.load(restored.headers["set-cookie"])
    assert _run(session.get_user_from_cookie(jar[COOKIE_NAME].value))["id"] == 8

    monkeypatch.setattr("auth.config.allowed_login_domain", lambda: "other.test")
    with pytest.raises(HTTPException) as exc:
        _run(admin.admin_stop_impersonation(_request(cookie)))
    assert exc.value.status_code == 403
