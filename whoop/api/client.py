"""
WHOOP API async HTTP client.
All requests go through here — auth injection, error handling, caching.
"""

import logging
from typing import Any

import httpx

from .cache import TTLCache
from .endpoints import BASE_URL

logger = logging.getLogger(__name__)


class WhoopAPIError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"WHOOP API {status_code}: {message}")


class WhoopClient:
    def __init__(
        self,
        token_getter,  # callable() -> str, may refresh token
        cache_ttl: int = 300,
    ) -> None:
        self._token_getter = token_getter
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

        token = self._token_getter()
        headers = {"Authorization": f"Bearer {token}"}

        logger.debug("GET %s params=%s", endpoint, params)
        resp = await self._http.get(endpoint, params=params, headers=headers)

        if resp.status_code == 401:
            # Token may have just expired between cache miss and request
            logger.info("Got 401, forcing token refresh")
            token = self._token_getter()
            headers = {"Authorization": f"Bearer {token}"}
            resp = await self._http.get(endpoint, params=params, headers=headers)

        self._raise_for_status(resp)

        data = resp.json()
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
        """
        results: list[dict] = []
        next_token: str | None = None
        base_params = dict(params or {})
        base_params["limit"] = limit

        for page in range(max_pages):
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
                break
        else:
            logger.warning(
                "get_paginated(%s) reached max_pages=%d, stopping early (got %d records)",
                endpoint,
                max_pages,
                len(results),
            )

        return results

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        try:
            detail = resp.json().get("message", resp.text)
        except Exception:
            detail = resp.text
        raise WhoopAPIError(resp.status_code, detail)
