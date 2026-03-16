"""
Integration tests for WhoopClient — httpx mocked via respx or unittest.mock.
Tests HTTP behaviour: auth, cache, pagination, error handling.
"""

import json
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from whoop.api.client import WhoopClient, WhoopAPIError


pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_token():
    return "test_access_token"


@pytest.fixture
def client(mock_token):
    c = WhoopClient(token_getter=lambda: mock_token, cache_ttl=300)
    return c


def _make_response(status_code: int, body: dict | list | str) -> httpx.Response:
    if isinstance(body, (dict, list)):
        content = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    else:
        content = body.encode()
        headers = {"content-type": "text/plain"}
    return httpx.Response(status_code, content=content, headers=headers)


class TestWhoopClientGet:
    async def test_successful_get_returns_json(self, client):
        expected = {"records": [{"id": 1}]}
        client._http.get = AsyncMock(return_value=_make_response(200, expected))

        result = await client.get("/v1/recovery")
        assert result == expected

    async def test_authorization_header_sent(self, client, mock_token):
        client._http.get = AsyncMock(return_value=_make_response(200, {}))

        await client.get("/v1/recovery", use_cache=False)
        call_kwargs = client._http.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("Authorization") == f"Bearer {mock_token}"

    async def test_cache_hit_skips_http(self, client):
        client._http.get = AsyncMock(return_value=_make_response(200, {"data": "fresh"}))

        # First call — should hit HTTP
        await client.get("/v1/profile")
        assert client._http.get.call_count == 1

        # Second call — should use cache
        await client.get("/v1/profile")
        assert client._http.get.call_count == 1  # still 1

    async def test_use_cache_false_always_fetches(self, client):
        client._http.get = AsyncMock(return_value=_make_response(200, {"fresh": True}))

        await client.get("/v1/profile", use_cache=False)
        await client.get("/v1/profile", use_cache=False)
        assert client._http.get.call_count == 2

    async def test_401_retried_with_new_token(self):
        tokens = iter(["expired_token", "fresh_token"])
        client = WhoopClient(token_getter=lambda: next(tokens), cache_ttl=0)

        responses = [
            _make_response(401, {"message": "Unauthorized"}),
            _make_response(200, {"ok": True}),
        ]
        client._http.get = AsyncMock(side_effect=responses)

        result = await client.get("/v1/recovery", use_cache=False)
        assert result == {"ok": True}
        assert client._http.get.call_count == 2

    async def test_401_on_retry_raises_api_error(self):
        client = WhoopClient(token_getter=lambda: "token", cache_ttl=0)
        client._http.get = AsyncMock(return_value=_make_response(401, {"message": "Unauthorized"}))

        with pytest.raises(WhoopAPIError) as exc_info:
            await client.get("/v1/recovery", use_cache=False)
        assert exc_info.value.status_code == 401

    async def test_429_raises_api_error(self, client):
        client._http.get = AsyncMock(return_value=_make_response(429, {"message": "Rate limited"}))

        with pytest.raises(WhoopAPIError) as exc_info:
            await client.get("/v1/recovery", use_cache=False)
        assert exc_info.value.status_code == 429

    async def test_500_raises_api_error(self, client):
        client._http.get = AsyncMock(return_value=_make_response(500, "Internal Server Error"))

        with pytest.raises(WhoopAPIError) as exc_info:
            await client.get("/v1/recovery", use_cache=False)
        assert exc_info.value.status_code == 500

    async def test_non_json_error_body_still_raises(self, client):
        client._http.get = AsyncMock(return_value=_make_response(503, "Service Unavailable"))

        with pytest.raises(WhoopAPIError):
            await client.get("/v1/recovery", use_cache=False)

    async def test_params_passed_to_http(self, client):
        client._http.get = AsyncMock(return_value=_make_response(200, {}))
        params = {"start": "2026-03-10T00:00:00Z"}

        await client.get("/v1/recovery", params=params, use_cache=False)
        call_kwargs = client._http.get.call_args
        sent_params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert sent_params == params

    async def test_aclose_called(self, client):
        client._http.aclose = AsyncMock()
        await client.aclose()
        client._http.aclose.assert_called_once()


class TestWhoopClientPaginated:
    async def test_single_page(self, client):
        response = {"records": [{"id": 1}, {"id": 2}], "next_token": None}
        client._http.get = AsyncMock(return_value=_make_response(200, response))

        results = await client.get_paginated("/v1/workout")
        assert len(results) == 2

    async def test_multiple_pages(self, client):
        pages = [
            {"records": [{"id": 1}], "next_token": "page2"},
            {"records": [{"id": 2}], "next_token": "page3"},
            {"records": [{"id": 3}], "next_token": None},
        ]
        client._http.get = AsyncMock(side_effect=[_make_response(200, p) for p in pages])

        results = await client.get_paginated("/v1/workout")
        assert [r["id"] for r in results] == [1, 2, 3]

    async def test_max_pages_stops_infinite_loop(self, client):
        infinite_page = {"records": [{"id": 1}], "next_token": "keep_going"}
        client._http.get = AsyncMock(return_value=_make_response(200, infinite_page))

        results = await client.get_paginated("/v1/workout", max_pages=3)
        assert len(results) == 3
        assert client._http.get.call_count == 3

    async def test_empty_records_returns_empty_list(self, client):
        client._http.get = AsyncMock(return_value=_make_response(200, {"records": [], "next_token": None}))

        results = await client.get_paginated("/v1/workout")
        assert results == []

    async def test_limit_passed_as_param(self, client):
        client._http.get = AsyncMock(return_value=_make_response(200, {"records": [], "next_token": None}))

        await client.get_paginated("/v1/workout", limit=10)
        call_kwargs = client._http.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params.get("limit") == 10
