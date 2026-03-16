"""
WhoopService — high-level facade used by both MCP tools and CLI commands.
Handles auth, API calls, mapping to unified schema, and analytics.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from .api.client import WhoopClient
from .api import endpoints
from .auth.oauth import OAuthConfig, WhoopOAuth
from .auth.token_store import TokenStore
from .analytics.daily_summary import DailySummary, build_daily_summary
from .analytics.trends import TrendReport, build_trends
from .schema.mappers import map_cycle, map_recovery, map_sleep, map_workout
from .schema.unified import DailyHealth

logger = logging.getLogger(__name__)


def _build_service_from_env() -> "WhoopService":
    """Factory: reads config from environment variables."""
    client_id = os.environ["WHOOP_CLIENT_ID"]
    client_secret = os.environ["WHOOP_CLIENT_SECRET"]
    redirect_uri = os.environ.get("WHOOP_REDIRECT_URI", "http://localhost:8080/callback")
    token_path = os.environ.get("WHOOP_TOKEN_PATH", "~/.whoop/tokens.enc")
    cache_ttl = int(os.environ.get("WHOOP_CACHE_TTL", "300"))

    store = TokenStore(token_path)
    oauth = WhoopOAuth(
        config=OAuthConfig(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        ),
        store=store,
    )
    client = WhoopClient(
        token_getter=oauth.ensure_valid_token,
        cache_ttl=cache_ttl,
    )
    return WhoopService(client=client, oauth=oauth)


class WhoopService:
    def __init__(self, client: WhoopClient, oauth: WhoopOAuth) -> None:
        self._client = client
        self._oauth = oauth

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def auth_status(self) -> dict:
        return self._oauth.token_status()

    def login(self) -> str:
        return self._oauth.ensure_valid_token()

    def login_headless(self) -> str:
        """Headless OAuth for VPS / bot. Prints URL, prompts for callback."""
        return self._oauth.authorize_headless()

    def logout(self) -> None:
        self._oauth.revoke()

    # ------------------------------------------------------------------
    # Raw data
    # ------------------------------------------------------------------

    async def get_profile(self) -> dict:
        return await self._client.get(endpoints.PROFILE)

    async def get_recovery(self, start: str | None = None, end: str | None = None) -> dict | None:
        params = self._date_params(start, end)
        data = await self._client.get(endpoints.RECOVERY, params=params)
        records = data.get("records", []) if isinstance(data, dict) else data
        return records[0] if records else None

    async def get_sleep(self, start: str | None = None, end: str | None = None) -> dict | None:
        params = self._date_params(start, end)
        data = await self._client.get(endpoints.SLEEP, params=params)
        records = data.get("records", []) if isinstance(data, dict) else data
        if not records:
            return None
        # Prefer non-nap records if nap field exists; otherwise take first scored
        mains = [r for r in records if not r.get("nap", False)]
        scored = [r for r in (mains or records) if r.get("score_state") == "SCORED"]
        return scored[0] if scored else (mains[0] if mains else records[0])

    async def get_workouts(self, start: str | None = None, end: str | None = None) -> list[dict]:
        params = self._date_params(start, end)
        return await self._client.get_paginated(endpoints.WORKOUT, params=params)

    async def get_cycles(self, start: str | None = None, end: str | None = None) -> list[dict]:
        params = self._date_params(start, end)
        return await self._client.get_paginated(endpoints.CYCLE, params=params)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    async def get_daily_summary(self, date: str | None = None) -> DailySummary:
        health = await self._get_daily_health(date)
        return build_daily_summary(health)

    async def get_trends(self, days: int = 7) -> TrendReport:
        if days < 1 or days > 90:
            raise ValueError("days must be between 1 and 90")

        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=days)
        start_str = start.strftime("%Y-%m-%dT00:00:00.000Z")
        end_str = end.strftime("%Y-%m-%dT23:59:59.000Z")

        # Fetch all data in parallel — 3 requests instead of N*2+1
        cycles, all_recoveries, all_sleeps = await asyncio.gather(
            self.get_cycles(start=start_str, end=end_str),
            self._client.get_paginated(
                endpoints.RECOVERY,
                params={"start": start_str, "end": end_str},
            ),
            self._client.get_paginated(
                endpoints.SLEEP,
                params={"start": start_str, "end": end_str},
            ),
        )

        # Build lookup tables: date -> first matching record
        recovery_by_date: dict[str, dict] = {}
        for r in all_recoveries:
            # Recovery records may have created_at or updated_at, not start
            d = (r.get("created_at") or r.get("start") or r.get("updated_at") or "")[:10]
            if d and d not in recovery_by_date:
                recovery_by_date[d] = r

        sleep_by_date: dict[str, dict] = {}
        for s in all_sleeps:
            if s.get("nap"):
                continue
            # Sleep records may have created_at instead of start
            d = (s.get("created_at") or s.get("start") or "")[:10]
            if d and d not in sleep_by_date:
                sleep_by_date[d] = s

        records: list[DailyHealth] = []
        for cycle in cycles:
            cycle_date = (cycle.get("start") or "")[:10]
            if not cycle_date:
                continue

            recovery_raw = recovery_by_date.get(cycle_date)
            sleep_raw = sleep_by_date.get(cycle_date)

            health = DailyHealth(
                source="whoop",
                date=cycle_date,
                fetched_at=datetime.now(tz=timezone.utc).isoformat(),
                sleep=map_sleep(sleep_raw) if sleep_raw else None,
                recovery=map_recovery(recovery_raw) if recovery_raw else None,
                activity=map_cycle(cycle),
            )
            records.append(health)

        return build_trends(records)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_daily_health(self, date: str | None) -> DailyHealth:
        if date is None:
            date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        start = f"{date}T00:00:00.000Z"
        end = f"{date}T23:59:59.000Z"

        # Fetch all independently in parallel
        recovery_raw, sleep_raw, workouts_raw, cycles_raw = await asyncio.gather(
            self.get_recovery(start=start, end=end),
            self.get_sleep(start=start, end=end),
            self.get_workouts(start=start, end=end),
            self.get_cycles(start=start, end=end),
        )

        activity = None
        if cycles_raw:
            activity = map_cycle(cycles_raw[0])
            activity.workouts = [map_workout(w) for w in workouts_raw]

        return DailyHealth(
            source="whoop",
            date=date,
            fetched_at=datetime.now(tz=timezone.utc).isoformat(),
            sleep=map_sleep(sleep_raw) if sleep_raw else None,
            recovery=map_recovery(recovery_raw) if recovery_raw else None,
            activity=activity,
        )

    @staticmethod
    def _date_params(start: str | None, end: str | None) -> dict | None:
        params: dict = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return params or None
