"""
Regression tests for whoop/auth/oauth.py: refresh locking, typed errors,
no interactive login in background paths, token response validation.
"""

import json
import multiprocessing
import os
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from whoop.auth.oauth import OAuthConfig, WhoopOAuth, WHOOP_TOKEN_URL
from whoop.auth.token_store import TokenData, TokenStore
from whoop.errors import ErrorCode, WhoopError

KEY_HEX = "a" * 64
CONFIG = OAuthConfig(client_id="cid", client_secret="csecret", redirect_uri="http://localhost:8080/callback")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
    return TokenStore(str(tmp_path / "tokens.enc"))


@pytest.fixture
def oauth(store):
    return WhoopOAuth(config=CONFIG, store=store)


@pytest.fixture(autouse=True)
def no_interactive(monkeypatch):
    """Any attempt to open a browser, read stdin or start a listener fails the test."""
    def _forbidden(*_a, **_k):
        raise AssertionError("interactive login attempted in a background path")

    monkeypatch.setattr("webbrowser.open", _forbidden)
    monkeypatch.setattr("builtins.input", _forbidden)
    monkeypatch.setattr("http.server.HTTPServer.__init__", _forbidden)


def _expired(access="old_access", refresh="old_refresh") -> TokenData:
    return TokenData(access_token=access, refresh_token=refresh, expires_at=time.time() - 10, scope="offline")


def _valid(access="old_access", refresh="old_refresh") -> TokenData:
    return TokenData(access_token=access, refresh_token=refresh, expires_at=time.time() + 3600, scope="offline")


def _token_response(status=200, body=None, headers=None) -> httpx.Response:
    request = httpx.Request("POST", WHOOP_TOKEN_URL)
    if body is None:
        body = {"access_token": "new_access", "refresh_token": "new_refresh", "expires_in": 3600, "scope": "offline read:profile"}
    content = json.dumps(body).encode() if not isinstance(body, bytes) else body
    return httpx.Response(status, content=content, headers=headers or {"content-type": "application/json"}, request=request)


# ---------------------------------------------------------------------------
# Refresh success paths
# ---------------------------------------------------------------------------

class TestRefresh:
    def test_valid_token_returned_without_network(self, oauth, store):
        store.save(_valid())
        with patch("whoop.auth.oauth.httpx.post") as post:
            assert oauth.get_access_token() == "old_access"
        post.assert_not_called()

    def test_expired_token_refreshed_and_rotated(self, oauth, store):
        store.save(_expired())
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_response()) as post:
            assert oauth.get_access_token() == "new_access"
        sent = post.call_args.kwargs["data"]
        assert sent["grant_type"] == "refresh_token"
        assert sent["refresh_token"] == "old_refresh"
        assert sent["scope"] == "offline"
        saved = store.load()
        assert saved.refresh_token == "new_refresh"
        assert saved.scope == "offline read:profile"

    def test_missing_refresh_token_in_response_keeps_previous(self, oauth, store):
        store.save(_expired())
        body = {"access_token": "new_access", "expires_in": 3600}
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_response(body=body)):
            oauth.get_access_token()
        assert store.load().refresh_token == "old_refresh"

    @pytest.mark.parametrize("body", [
        {"refresh_token": "r", "expires_in": 3600},
        {"access_token": "", "refresh_token": "r", "expires_in": 3600},
        {"access_token": "a", "refresh_token": "r"},
        {"access_token": "a", "refresh_token": "r", "expires_in": "3600"},
        {"access_token": "a", "refresh_token": "r", "expires_in": 0},
    ])
    def test_malformed_response_not_saved(self, oauth, store, body):
        store.save(_expired())
        before = store.path.read_bytes()
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_response(body=body)):
            with pytest.raises(WhoopError) as exc_info:
                oauth.get_access_token()
        assert exc_info.value.code == ErrorCode.API_ERROR
        assert store.path.read_bytes() == before

    def test_non_json_success_body_not_saved(self, oauth, store):
        store.save(_expired())
        before = store.path.read_bytes()
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_response(body=b"<html>")):
            with pytest.raises(WhoopError):
                oauth.get_access_token()
        assert store.path.read_bytes() == before


# ---------------------------------------------------------------------------
# Failure classification — tokens are never destroyed, no interactive login
# ---------------------------------------------------------------------------

class TestRefreshFailures:
    @pytest.mark.parametrize("status,error,code", [
        (400, "invalid_grant", ErrorCode.REAUTH_REQUIRED),
        (400, "unauthorized_client", ErrorCode.REAUTH_REQUIRED),
        (400, "invalid_request", ErrorCode.REAUTH_REQUIRED),
        (401, "invalid_request", ErrorCode.API_ERROR),
        (403, None, ErrorCode.TRANSIENT_ERROR),
        (408, None, ErrorCode.TRANSIENT_ERROR),
        (401, "invalid_grant", ErrorCode.REAUTH_REQUIRED),
        (400, None, ErrorCode.REAUTH_REQUIRED),
        (401, "invalid_client", ErrorCode.CONFIG_ERROR),
        (400, "invalid_scope", ErrorCode.INSUFFICIENT_SCOPE),
        (500, None, ErrorCode.TRANSIENT_ERROR),
        (503, None, ErrorCode.TRANSIENT_ERROR),
    ])
    def test_http_errors_classified_and_tokens_kept(self, oauth, store, status, error, code):
        store.save(_expired())
        before = store.path.read_bytes()
        body = {"error": error, "error_description": "echo old_refresh"} if error else {}
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_response(status, body)) as post:
            with pytest.raises(WhoopError) as exc_info:
                oauth.get_access_token()
        assert exc_info.value.code == code
        assert post.call_count == 1  # no retry loop
        assert store.path.read_bytes() == before
        assert "old_refresh" not in str(exc_info.value)
        assert "csecret" not in str(exc_info.value)

    def test_save_failure_after_rotation_is_typed_and_retried(self, oauth, store, monkeypatch):
        store.save(_expired())
        attempts = []

        def _failing_save(tokens):
            attempts.append(tokens)
            raise WhoopError(ErrorCode.TOKEN_STORE_ERROR, "Cannot write token file: No space left on device")

        monkeypatch.setattr(store, "save", _failing_save)
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_response()):
            with pytest.raises(WhoopError) as exc_info:
                oauth.get_access_token()
        assert exc_info.value.code == ErrorCode.TOKEN_STORE_ERROR
        assert len(attempts) == 2
        assert "new_refresh" not in str(exc_info.value)

    def test_429_rate_limited_with_retry_after(self, oauth, store):
        store.save(_expired())
        resp = _token_response(429, {}, headers={"Retry-After": "42", "content-type": "application/json"})
        with patch("whoop.auth.oauth.httpx.post", return_value=resp):
            with pytest.raises(WhoopError) as exc_info:
                oauth.get_access_token()
        assert exc_info.value.code == ErrorCode.RATE_LIMITED
        assert exc_info.value.retry_after == 42
        assert store.load().refresh_token == "old_refresh"

    @pytest.mark.parametrize("exc", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t"), httpx.ConnectError("refused")])
    def test_network_errors_are_transient(self, oauth, store, exc):
        store.save(_expired())
        with patch("whoop.auth.oauth.httpx.post", side_effect=exc):
            with pytest.raises(WhoopError) as exc_info:
                oauth.get_access_token()
        assert exc_info.value.code == ErrorCode.TRANSIENT_ERROR
        assert store.load().refresh_token == "old_refresh"

    def test_no_tokens_is_auth_required_without_interaction(self, oauth):
        with pytest.raises(WhoopError) as exc_info:
            oauth.get_access_token()
        assert exc_info.value.code == ErrorCode.AUTH_REQUIRED

    def test_expired_without_refresh_token_is_reauth_required(self, oauth, store):
        store.save(_expired(refresh=""))
        with patch("whoop.auth.oauth.httpx.post") as post:
            with pytest.raises(WhoopError) as exc_info:
                oauth.get_access_token()
        assert exc_info.value.code == ErrorCode.REAUTH_REQUIRED
        post.assert_not_called()

    def test_wrong_key_is_store_error_and_file_kept(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
        TokenStore(str(tmp_path / "tokens.enc")).save(_valid())
        monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", "b" * 64)
        oauth = WhoopOAuth(CONFIG, TokenStore(str(tmp_path / "tokens.enc")))
        with pytest.raises(WhoopError) as exc_info:
            oauth.get_access_token()
        assert exc_info.value.code == ErrorCode.TOKEN_STORE_ERROR
        assert (tmp_path / "tokens.enc").exists()
        assert oauth.token_status()["state"] == "store_error"


# ---------------------------------------------------------------------------
# force_refresh after 401
# ---------------------------------------------------------------------------

class TestForceRefresh:
    def test_refreshes_when_stored_token_is_the_rejected_one(self, oauth, store):
        store.save(_valid(access="rejected"))
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_response()) as post:
            assert oauth.force_refresh("rejected") == "new_access"
        assert post.call_count == 1

    def test_reuses_token_rotated_by_another_process(self, oauth, store):
        store.save(_valid(access="rotated_elsewhere"))
        with patch("whoop.auth.oauth.httpx.post") as post:
            assert oauth.force_refresh("rejected") == "rotated_elsewhere"
        post.assert_not_called()


# ---------------------------------------------------------------------------
# Explicit login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_code_exchange_without_refresh_token_not_saved(self, oauth, store):
        _, state, verifier = oauth.get_auth_url()
        body = {"access_token": "a", "expires_in": 3600}
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_response(body=body)):
            with pytest.raises(WhoopError) as exc_info:
                oauth.exchange_callback(f"http://localhost:8080/callback?code=c&state={state}", state, verifier)
        assert exc_info.value.code == ErrorCode.INSUFFICIENT_SCOPE
        assert not store.path.exists()

    def test_code_exchange_saves_tokens(self, oauth, store):
        _, state, verifier = oauth.get_auth_url()
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_response()):
            oauth.exchange_callback(f"http://localhost:8080/callback?code=c&state={state}", state, verifier)
        assert store.load().refresh_token == "new_refresh"

    def test_state_mismatch_does_not_echo_state(self, oauth):
        with pytest.raises(RuntimeError) as exc_info:
            oauth.exchange_callback("http://localhost:8080/callback?code=c&state=evil", "expected_secret_state", "v")
        assert "expected_secret_state" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Multi-process refresh race
# ---------------------------------------------------------------------------

def _fake_whoop_post(server_dir: Path):
    """Simulates WHOOP: only the current refresh token is accepted; each use rotates it."""

    def post(url, data, headers, timeout):
        import fcntl
        request = httpx.Request("POST", url)
        with open(server_dir / "server.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state_file = server_dir / "server.json"
            state = json.loads(state_file.read_text())
            time.sleep(0.05)  # widen the race window
            if data["refresh_token"] != state["current"]:
                state["rejected"] += 1
                state_file.write_text(json.dumps(state))
                return httpx.Response(400, json={"error": "invalid_grant"}, request=request)
            state["n"] += 1
            state["current"] = f"refresh_{state['n']}"
            state_file.write_text(json.dumps(state))
            return httpx.Response(200, json={
                "access_token": f"access_{state['n']}",
                "refresh_token": state["current"],
                "expires_in": 3600,
            }, request=request)

    return post


def _worker(token_path: str, server_dir: str, result_queue) -> None:
    os.environ["WHOOP_TOKEN_ENCRYPTION_KEY"] = KEY_HEX
    oauth = WhoopOAuth(CONFIG, TokenStore(token_path))
    with patch("whoop.auth.oauth.httpx.post", _fake_whoop_post(Path(server_dir))):
        try:
            result_queue.put(("ok", oauth.get_access_token()))
        except WhoopError as exc:
            result_queue.put(("error", exc.code.value))


class TestMultiProcessRefresh:
    def test_parallel_refresh_does_not_lose_rotated_tokens(self, tmp_path, store):
        store.save(_expired(refresh="refresh_0"))
        (tmp_path / "server.json").write_text(json.dumps({"current": "refresh_0", "n": 0, "rejected": 0}))

        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        procs = [
            ctx.Process(target=_worker, args=(str(store.path), str(tmp_path), queue))
            for _ in range(6)
        ]
        for p in procs:
            p.start()
        results = [queue.get(timeout=60) for _ in procs]
        for p in procs:
            p.join(timeout=30)

        server = json.loads((tmp_path / "server.json").read_text())
        assert all(kind == "ok" for kind, _ in results), results
        assert {value for _, value in results} == {"access_1"}
        assert server["n"] == 1  # exactly one refresh
        assert server["rejected"] == 0  # nobody reused a rotated refresh token
        assert store.load().refresh_token == server["current"]
