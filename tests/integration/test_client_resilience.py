"""
Regression tests: WhoopClient + real WhoopOAuth/TokenStore under 401, 429, 5xx,
timeouts. WHOOP endpoints are mocked; no live secrets are needed.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from whoop.api.client import WhoopAPIError, WhoopClient
from whoop.auth.oauth import OAuthConfig, WhoopOAuth, WHOOP_TOKEN_URL
from whoop.auth.token_store import TokenData, TokenStore
from whoop.errors import ErrorCode, WhoopError

pytestmark = pytest.mark.asyncio

KEY_HEX = "a" * 64


def _resp(status: int, body=None, headers=None) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(body if body is not None else {}).encode(),
        headers={"content-type": "application/json", **(headers or {})},
        request=httpx.Request("GET", "https://example.test"),
    )


def _token_resp(n: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": f"access_{n}", "refresh_token": f"refresh_{n}", "expires_in": 3600},
        request=httpx.Request("POST", WHOOP_TOKEN_URL),
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
    s = TokenStore(str(tmp_path / "tokens.enc"))
    # Locally the token looks valid for another hour
    s.save(TokenData(access_token="access_0", refresh_token="refresh_0", expires_at=time.time() + 3600))
    return s


@pytest.fixture
def client(store):
    oauth = WhoopOAuth(OAuthConfig("cid", "csecret", "http://localhost:8080/callback"), store)
    c = WhoopClient(token_getter=oauth.get_access_token, token_refresher=oauth.force_refresh, cache_ttl=0)
    c.sleeps = []

    async def _sleep(seconds):
        c.sleeps.append(seconds)

    c._sleep = _sleep
    return c


def _bearer(call) -> str:
    return call.kwargs["headers"]["Authorization"].removeprefix("Bearer ")


class TestUnauthorizedRecovery:
    async def test_401_with_future_expiry_refreshes_once_and_retries(self, client, store):
        client._http.get = AsyncMock(side_effect=[_resp(401), _resp(200, {"ok": True})])
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_resp(1)) as post:
            assert await client.get("/v2/x", use_cache=False) == {"ok": True}

        assert post.call_count == 1
        assert [_bearer(c) for c in client._http.get.call_args_list] == ["access_0", "access_1"]
        assert store.load().refresh_token == "refresh_1"

    async def test_repeated_401_is_reauth_required(self, client):
        client._http.get = AsyncMock(return_value=_resp(401, {"message": "Unauthorized"}))
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_resp(1)) as post:
            with pytest.raises(WhoopAPIError) as exc_info:
                await client.get("/v2/x", use_cache=False)

        assert exc_info.value.code == ErrorCode.REAUTH_REQUIRED
        assert client._http.get.call_count == 2
        assert post.call_count == 1

    async def test_401_then_refresh_rejected_is_reauth_required(self, client, store):
        client._http.get = AsyncMock(return_value=_resp(401))
        bad = httpx.Response(400, json={"error": "invalid_grant"}, request=httpx.Request("POST", WHOOP_TOKEN_URL))
        with patch("whoop.auth.oauth.httpx.post", return_value=bad):
            with pytest.raises(WhoopError) as exc_info:
                await client.get("/v2/x", use_cache=False)
        assert exc_info.value.code == ErrorCode.REAUTH_REQUIRED
        assert store.load().refresh_token == "refresh_0"  # nothing destroyed

    async def test_concurrent_401s_refresh_only_once(self, client):
        async def _get(endpoint, params, headers):
            await asyncio.sleep(0)
            return _resp(401) if headers["Authorization"] == "Bearer access_0" else _resp(200, {"ok": 1})

        client._http.get = _get
        with patch("whoop.auth.oauth.httpx.post", return_value=_token_resp(1)) as post:
            results = await asyncio.gather(*[client.get(f"/v2/{i}", use_cache=False) for i in range(4)])
        assert results == [{"ok": 1}] * 4
        assert post.call_count == 1

    async def test_403_is_insufficient_scope(self, client):
        client._http.get = AsyncMock(return_value=_resp(403, {"message": "Forbidden"}))
        with pytest.raises(WhoopAPIError) as exc_info:
            await client.get("/v2/x", use_cache=False)
        assert exc_info.value.code == ErrorCode.INSUFFICIENT_SCOPE


class TestTransientFailures:
    async def test_429_honours_retry_after(self, client):
        client._http.get = AsyncMock(side_effect=[_resp(429, headers={"Retry-After": "3"}), _resp(200, {"ok": 1})])
        assert await client.get("/v2/x", use_cache=False) == {"ok": 1}
        assert client.sleeps == [3.0]

    async def test_429_with_long_retry_after_fails_fast(self, client, store):
        client._http.get = AsyncMock(return_value=_resp(429, headers={"Retry-After": "120"}))
        with patch("whoop.auth.oauth.httpx.post") as post:
            with pytest.raises(WhoopAPIError) as exc_info:
                await client.get("/v2/x", use_cache=False)
        assert exc_info.value.code == ErrorCode.RATE_LIMITED
        assert exc_info.value.retry_after == 120
        assert client.sleeps == []
        post.assert_not_called()
        assert store.load().access_token == "access_0"

    async def test_429_retries_are_bounded(self, client):
        client._http.get = AsyncMock(return_value=_resp(429, headers={"Retry-After": "1"}))
        with pytest.raises(WhoopAPIError):
            await client.get("/v2/x", use_cache=False)
        assert client._http.get.call_count == 3

    async def test_5xx_retried_then_transient_error(self, client, store):
        client._http.get = AsyncMock(return_value=_resp(503))
        with patch("whoop.auth.oauth.httpx.post") as post:
            with pytest.raises(WhoopAPIError) as exc_info:
                await client.get("/v2/x", use_cache=False)
        assert exc_info.value.code == ErrorCode.TRANSIENT_ERROR
        assert client._http.get.call_count == 3
        post.assert_not_called()
        assert store.load().refresh_token == "refresh_0"

    async def test_5xx_then_success(self, client):
        client._http.get = AsyncMock(side_effect=[_resp(502), _resp(200, {"ok": 1})])
        assert await client.get("/v2/x", use_cache=False) == {"ok": 1}

    async def test_timeout_retried_then_transient_error(self, client, store):
        client._http.get = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(WhoopError) as exc_info:
            await client.get("/v2/x", use_cache=False)
        assert exc_info.value.code == ErrorCode.TRANSIENT_ERROR
        assert client._http.get.call_count == 3
        assert store.load().refresh_token == "refresh_0"


class TestMalformedResponses:
    async def test_non_json_success_body_is_api_error(self, client):
        html = httpx.Response(200, content=b"<html>", request=httpx.Request("GET", "https://example.test"))
        client._http.get = AsyncMock(return_value=html)
        with pytest.raises(WhoopError) as exc_info:
            await client.get("/v2/x", use_cache=False)
        assert exc_info.value.code == ErrorCode.API_ERROR


class TestEventLoop:
    async def test_blocking_token_refresh_does_not_block_loop(self):
        ticks = 0
        ticks_when_getter_done = None

        def slow_getter():
            nonlocal ticks_when_getter_done
            time.sleep(0.3)
            ticks_when_getter_done = ticks
            return "t"

        client = WhoopClient(token_getter=slow_getter, cache_ttl=0)
        client._http.get = AsyncMock(return_value=_resp(200, {}))

        async def ticker():
            nonlocal ticks
            for _ in range(30):
                await asyncio.sleep(0.02)
                ticks += 1

        await asyncio.gather(client.get("/v2/x", use_cache=False), ticker())
        # A blocked loop would show 0 ticks while the getter was sleeping
        assert ticks_when_getter_done >= 5
