"""
WHOOP API async HTTP client.
All requests go through here — auth injection, error handling, caching.

Retry policy (GET only, all idempotent):
- 401: one forced token refresh, then one retry; a second 401 is REAUTH_REQUIRED;
- 429: honour Retry-After up to _MAX_RETRY_AFTER seconds, at most _MAX_RATE_RETRIES times;
- timeout / network / 5xx: at most _MAX_TRANSIENT_RETRIES retries with backoff.
"""

import asyncio
import logging
from typing import Callable

import httpx

from whoop.errors import ErrorCode, WhoopError, parse_retry_after

from .cache import TTLCache
from .endpoints import BASE_URL

logger = logging.getLogger(__name__)

_MAX_TRANSIENT_RETRIES = 2
_TRANSIENT_BACKOFF_SECONDS = (0.5, 2.0)
_MAX_RATE_RETRIES = 2
_MAX_RETRY_AFTER = 10.0
_DEFAULT_RETRY_AFTER = 1.0
_MAX_ERROR_DETAIL = 200


def _code_for_status(status_code: int) -> ErrorCode:
    if status_code == 401:
        return ErrorCode.REAUTH_REQUIRED
    if status_code == 403:
        return ErrorCode.INSUFFICIENT_SCOPE
    if status_code == 429:
        return ErrorCode.RATE_LIMITED
    if status_code >= 500:
        return ErrorCode.TRANSIENT_ERROR
    return ErrorCode.API_ERROR


class WhoopAPIError(WhoopError):
    def __init__(self, status_code: int, message: str, retry_after: float | None = None) -> None:
        self.status_code = status_code
        super().__init__(
            _code_for_status(status_code),
            f"WHOOP API {status_code}: {message}",
            retry_after=retry_after,
        )


class WhoopClient:
    def __init__(
        self,
        token_getter: Callable[[], str],
        token_refresher: Callable[[str], str] | None = None,
        cache_ttl: int = 300,
    ) -> None:
        """
        token_getter: returns a valid access token (may refresh, may block).
        token_refresher: called with the rejected token after a 401; returns a new one.
        Both are sync and run in a worker thread so the event loop is never blocked.
        """
        self._token_getter = token_getter
        self._token_refresher = token_refresher
        self._token_lock = asyncio.Lock()
        self._sleep = asyncio.sleep  # replaceable in tests
        self._cache = TTLCache(ttl_seconds=cache_ttl)
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=30.0,
            headers={"User-Agent": "whoop-connecter/0.1"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def get(
        self,
        endpoint: str,
        params: dict | None = None,
        use_cache: bool = True,
    ) -> dict | list:
        if use_cache:
            cached = self._cache.get(endpoint, params)
            if cached is not None:
                logger.debug("Cache hit: %s", endpoint)
                return cached

        data = await self._request(endpoint, params)
        if use_cache:
            self._cache.set(endpoint, data, params)
        return data

    async def get_paginated(
        self,
        endpoint: str,
        params: dict | None = None,
        limit: int = 25,
        max_pages: int = 50,
    ) -> list[dict]:
        """Fetch all pages from a paginated WHOOP endpoint.

        max_pages guards against infinite loops caused by buggy next_token responses.
        Hitting it raises INCOMPLETE_DATA instead of silently returning a partial list.
        """
        results: list[dict] = []
        next_token: str | None = None
        base_params = dict(params or {})
        base_params["limit"] = limit

        for _ in range(max_pages):
            page_params = dict(base_params)
            if next_token:
                page_params["nextToken"] = next_token

            data = await self.get(endpoint, params=page_params, use_cache=False)

            if isinstance(data, dict):
                records = data.get("records", [])
                next_token = data.get("next_token")
            else:
                records = data
                next_token = None

            results.extend(records)

            if not next_token:
                return results

        raise WhoopError(
            ErrorCode.INCOMPLETE_DATA,
            f"{endpoint}: stopped after {max_pages} pages ({len(results)} records), more data available",
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _request(self, endpoint: str, params: dict | None) -> dict | list:
        token = await self._get_token()
        refreshed = False
        transient_retries = 0
        rate_retries = 0

        while True:
            logger.debug("GET %s", endpoint)
            try:
                resp = await self._http.get(
                    endpoint, params=params, headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.TransportError as exc:  # includes timeouts
                if transient_retries < _MAX_TRANSIENT_RETRIES:
                    await self._sleep(_TRANSIENT_BACKOFF_SECONDS[transient_retries])
                    transient_retries += 1
                    continue
                raise WhoopError(
                    ErrorCode.TRANSIENT_ERROR, f"{endpoint}: network error ({type(exc).__name__})"
                ) from None

            status = resp.status_code

            if status == 401 and not refreshed and self._token_refresher is not None:
                logger.info("Got 401, forcing token refresh")
                token = await self._refresh_token(token)
                refreshed = True
                continue

            if status == 429:
                retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                wait = _DEFAULT_RETRY_AFTER if retry_after is None else retry_after
                if rate_retries < _MAX_RATE_RETRIES and wait <= _MAX_RETRY_AFTER:
                    logger.info("Got 429, retrying after %.1fs", wait)
                    await self._sleep(wait)
                    rate_retries += 1
                    continue
                self._raise_for_status(resp, retry_after=retry_after)

            if status >= 500 and transient_retries < _MAX_TRANSIENT_RETRIES:
                await self._sleep(_TRANSIENT_BACKOFF_SECONDS[transient_retries])
                transient_retries += 1
                continue

            self._raise_for_status(resp)
            try:
                return resp.json()
            except ValueError:
                raise WhoopError(ErrorCode.API_ERROR, f"{endpoint}: response is not valid JSON") from None

    async def _get_token(self) -> str:
        async with self._token_lock:
            return await asyncio.to_thread(self._token_getter)

    async def _refresh_token(self, rejected: str) -> str:
        async with self._token_lock:
            return await asyncio.to_thread(self._token_refresher, rejected)

    @staticmethod
    def _raise_for_status(resp: httpx.Response, retry_after: float | None = None) -> None:
        if resp.is_success:
            return
        try:
            body = resp.json()
            detail = body.get("message", resp.text) if isinstance(body, dict) else resp.text
        except ValueError:
            detail = resp.text
        raise WhoopAPIError(resp.status_code, str(detail)[:_MAX_ERROR_DETAIL], retry_after=retry_after)
