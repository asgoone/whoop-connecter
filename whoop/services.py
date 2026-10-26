"""
WhoopService — high-level facade used by both MCP tools and CLI commands.
Handles auth, API calls, mapping to unified schema, and analytics.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date as date_cls, datetime, timedelta, timezone

from .api.client import WhoopClient
from .api import endpoints
from .auth.oauth import OAuthConfig, WhoopOAuth
from .auth.token_store import TokenStore
from .errors import WhoopError
from .analytics.daily_summary import DailySummary, build_daily_summary
from .analytics.trends import TrendReport, build_trends
from .schema.mappers import map_body_measurement, map_cycle, map_recovery, map_sleep, map_workout
from .schema.unified import DailyHealth

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_offset(value: str | None) -> timezone:
    """WHOOP TZD offset ('+03:00', '-05:00', 'Z') -> tzinfo; UTC if absent or invalid."""
    if not value or value == "Z":
        return timezone.utc
    try:
        sign = -1 if value[0] == "-" else 1
        hours, minutes = value.lstrip("+-").split(":")
        return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
    except ValueError:
        return timezone.utc


def _local_date(value: str | None, tz: timezone) -> str | None:
    ts = _parse_ts(value)
    return ts.astimezone(tz).date().isoformat() if ts else None


def _cycle_day(cycle: dict) -> str | None:
    """Local calendar day a cycle belongs to.

    A cycle starts at sleep onset. Onset before local noon belongs to that
    day; onset after local noon (going to bed in the evening) belongs to the
    next day, i.e. the day the user wakes up.
    """
    start = _parse_ts(cycle.get("start"))
    if start is None:
        return None
    local = start.astimezone(_parse_offset(cycle.get("timezone_offset")))
    if local.hour >= 12:
        local += timedelta(days=1)
    return local.date().isoformat()


def _record_for_cycle(records: list[dict], cycle: dict, day: str, ts_key: str) -> dict | None:
    """First record linked to the cycle by cycle_id.

    Records without cycle_id fall back to the local date of `ts_key`
    (or created_at) in the cycle's timezone.
    """
    tz = _parse_offset(cycle.get("timezone_offset"))
    for r in records:
        if r.get("cycle_id") is not None:
            if r["cycle_id"] == cycle.get("id"):
                return r
        elif _local_date(r.get(ts_key) or r.get("created_at"), tz) == day:
            return r
    return None


def _build_daily_health(
    cycle: dict, day: str, recoveries: list[dict], sleeps: list[dict]
) -> DailyHealth:
    main_sleeps = [s for s in sleeps if not s.get("nap")]
    recovery_raw = _record_for_cycle(recoveries, cycle, day, "created_at")
    sleep_raw = _record_for_cycle(main_sleeps, cycle, day, "end")
    return DailyHealth(
        source="whoop",
        date=day,
        fetched_at=_utcnow().isoformat(),
        sleep=map_sleep(sleep_raw) if sleep_raw else None,
        recovery=map_recovery(recovery_raw) if recovery_raw else None,
        activity=map_cycle(cycle),
    )


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
        token_getter=oauth.get_access_token,
        token_refresher=oauth.force_refresh,
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
        """Passive status from the token file: no network, no refresh."""
        return self._oauth.token_status()

    async def auth_check(self) -> dict:
        """Active check: refreshes if needed and calls the profile endpoint.

        Returns the passive status plus "check": {"ok": bool, ...error}.
        Never returns profile data.
        """
        try:
            await self._client.get(endpoints.PROFILE, use_cache=False)
            check = {"ok": True}
        except WhoopError as exc:
            check = {"ok": False, **exc.to_dict()}
        return {**self._oauth.token_status(), "check": check}

    def login(self) -> None:
        """Explicit interactive login with a local browser."""
        self._oauth.login_browser()

    def login_headless(self) -> None:
        """Explicit headless login for VPS: prints URL, prompts for callback URL."""
        self._oauth.login_headless()

    def logout(self) -> None:
        self._oauth.revoke()

    # ------------------------------------------------------------------
    # Raw data
    # ------------------------------------------------------------------

    async def get_profile(self) -> dict:
        return await self._client.get(endpoints.PROFILE)

    async def get_body_measurement(self) -> dict:
        return await self._client.get(endpoints.BODY_MEASUREMENT)

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
        records = await self._fetch_daily_records_batch(days)
        return build_trends(records)

    async def get_export(self, days: int = 7) -> dict:
        """Export body + N days of daily health data.

        Uses batch API requests (4 total) instead of per-day fetching.
        """
        if days < 1 or days > 90:
            raise ValueError("days must be between 1 and 90")

        body_raw, records = await asyncio.gather(
            self.get_body_measurement(),
            self._fetch_daily_records_batch(days),
        )

        body = map_body_measurement(body_raw)

        return {
            "export_date": _utcnow().isoformat(),
            "days": days,
            "body": body.to_dict(),
            "daily": [r.to_dict() for r in records],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_daily_records_batch(self, days: int) -> list[DailyHealth]:
        """Fetch N days of daily health records using 3 batch API calls."""
        end = _utcnow()
        start = end - timedelta(days=days)
        start_str = start.strftime("%Y-%m-%dT00:00:00.000Z")
        end_str = end.strftime("%Y-%m-%dT23:59:59.000Z")

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

        records: list[DailyHealth] = []
        for cycle in cycles:
            day = _cycle_day(cycle)
            if day:
                records.append(_build_daily_health(cycle, day, all_recoveries, all_sleeps))
        return records

    async def _get_daily_health(self, date: str | None) -> DailyHealth:
        """Health for one local calendar day.

        WHOOP filters by UTC record start, while days are local to the user
        (each record carries timezone_offset). Query a window wide enough for
        any offset, then pick the cycle whose local day matches and link its
        sleep/recovery by cycle_id.
        """
        if date is None:
            anchor = _utcnow().date()
        else:
            try:
                anchor = date_cls.fromisoformat(date)
            except ValueError:
                raise ValueError("date must be in YYYY-MM-DD format") from None

        start = (anchor - timedelta(days=3)).strftime("%Y-%m-%dT00:00:00.000Z")
        end = (anchor + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00.000Z")
        params = {"start": start, "end": end}

        recoveries, sleeps, workouts_raw, cycles = await asyncio.gather(
            self._client.get_paginated(endpoints.RECOVERY, params=params),
            self._client.get_paginated(endpoints.SLEEP, params=params),
            self.get_workouts(start=start, end=end),
            self.get_cycles(start=start, end=end),
        )

        if date is None:
            date = self._local_today(cycles)

        cycle = next((c for c in cycles if _cycle_day(c) == date), None)
        if cycle is None:
            return DailyHealth(source="whoop", date=date, fetched_at=_utcnow().isoformat())

        health = _build_daily_health(cycle, date, recoveries, sleeps)
        cycle_start = _parse_ts(cycle.get("start"))
        cycle_end = _parse_ts(cycle.get("end"))
        health.activity.workouts = [
            map_workout(w) for w in workouts_raw
            if (ws := _parse_ts(w.get("start"))) is not None
            and ws >= cycle_start
            and (cycle_end is None or ws < cycle_end)
        ]
        return health

    @staticmethod
    def _local_today(cycles: list[dict]) -> str:
        """Today's date in the user's timezone, taken from the latest cycle (UTC if none)."""
        latest = max(
            (c for c in cycles if _parse_ts(c.get("start"))),
            key=lambda c: _parse_ts(c["start"]),
            default=None,
        )
        tz = _parse_offset(latest.get("timezone_offset")) if latest else timezone.utc
        return _utcnow().astimezone(tz).date().isoformat()

    @staticmethod
    def _date_params(start: str | None, end: str | None) -> dict | None:
        params: dict = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return params or None
