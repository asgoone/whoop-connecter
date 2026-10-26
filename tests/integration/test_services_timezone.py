"""
Day-boundary tests for users outside UTC.

WHOOP v2 records carry UTC timestamps plus the user's `timezone_offset`.
The fake client below emulates the documented collection filter
(`start` <= record start < `end`), so a query window in the wrong
timezone silently drops records — exactly what happened in production
for a UTC+03:00 user whose sleep started just before 00:00 UTC.

All values are synthetic.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from whoop.api import endpoints
from whoop.api.client import WhoopClient
from whoop.services import WhoopService


pytestmark = pytest.mark.asyncio

TZ = "+03:00"


def _cycle(cid, start, end, strain):
    return {
        "id": cid, "start": start, "end": end, "timezone_offset": TZ,
        "created_at": start, "score_state": "SCORED",
        "score": {"strain": strain, "kilojoule": 8000.0,
                  "average_heart_rate": 60, "max_heart_rate": 150},
    }


def _sleep(sid, cid, start, end, perf, nap=False):
    return {
        "id": sid, "cycle_id": cid, "start": start, "end": end,
        "timezone_offset": TZ, "nap": nap, "created_at": end,
        "score_state": "SCORED",
        "score": {"sleep_performance_percentage": perf,
                  "sleep_efficiency_percentage": 90.0,
                  "stage_summary": {"total_in_bed_time_milli": 25_000_000}},
    }


def _recovery(cid, sid, created_at, score):
    return {
        "cycle_id": cid, "sleep_id": sid, "created_at": created_at,
        "score_state": "SCORED",
        "score": {"recovery_score": score, "hrv_rmssd_milli": 50.0,
                  "resting_heart_rate": 55},
    }


def _workout(start, end, strain):
    return {
        "sport_id": 0, "start": start, "end": end, "timezone_offset": TZ,
        "score_state": "SCORED",
        "score": {"strain": strain, "average_heart_rate": 140,
                  "max_heart_rate": 170, "kilojoule": 1500.0},
    }


# Night of Oct 25 -> Oct 26 (local +03:00): onset 02:55 local = 23:55 UTC the day before.
PREV_CYCLE = _cycle(1, "2026-10-24T23:10:00.000Z", "2026-10-25T23:55:59.000Z", 9.0)
CUR_CYCLE = _cycle(2, "2026-10-25T23:55:59.000Z", None, 11.0)
PREV_SLEEP = _sleep(11, 1, "2026-10-24T23:10:00.000Z", "2026-10-25T05:30:00.000Z", 60)
CUR_SLEEP = _sleep(22, 2, "2026-10-25T23:55:59.000Z", "2026-10-26T06:09:00.000Z", 88)
CUR_NAP = _sleep(23, 2, "2026-10-26T12:00:00.000Z", "2026-10-26T12:30:00.000Z", 99, nap=True)
PREV_RECOVERY = _recovery(1, 11, "2026-10-25T05:35:00.000Z", 40)
CUR_RECOVERY = _recovery(2, 22, "2026-10-26T06:15:00.000Z", 77)
CUR_WORKOUT = _workout("2026-10-26T14:00:00.000Z", "2026-10-26T15:00:00.000Z", 8.0)
PREV_WORKOUT = _workout("2026-10-25T14:00:00.000Z", "2026-10-25T15:00:00.000Z", 5.0)


def _fake_client(cycles, sleeps, recoveries, workouts):
    """Client double that routes by endpoint and applies WHOOP's start/end filter."""
    cycle_start = {c["id"]: c["start"] for c in cycles}

    def record_start(endpoint, rec):
        if endpoint == endpoints.RECOVERY:
            return cycle_start.get(rec["cycle_id"], rec["created_at"])
        return rec["start"]

    def select(endpoint, params):
        data = {
            endpoints.CYCLE: cycles,
            endpoints.SLEEP: sleeps,
            endpoints.RECOVERY: recoveries,
            endpoints.WORKOUT: workouts,
        }[endpoint]
        params = params or {}
        lo, hi = params.get("start"), params.get("end")

        def ts(s):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))

        out = [
            r for r in data
            if (lo is None or ts(record_start(endpoint, r)) >= ts(lo))
            and (hi is None or ts(record_start(endpoint, r)) < ts(hi))
        ]
        # WHOOP returns newest first
        return sorted(out, key=lambda r: ts(record_start(endpoint, r)), reverse=True)

    async def get(endpoint, params=None, **_):
        return {"records": select(endpoint, params)}

    async def get_paginated(endpoint, params=None, **_):
        return select(endpoint, params)

    client = MagicMock(spec=WhoopClient)
    client.get = AsyncMock(side_effect=get)
    client.get_paginated = AsyncMock(side_effect=get_paginated)
    return client


@pytest.fixture
def service():
    client = _fake_client(
        cycles=[PREV_CYCLE, CUR_CYCLE],
        sleeps=[PREV_SLEEP, CUR_SLEEP, CUR_NAP],
        recoveries=[PREV_RECOVERY, CUR_RECOVERY],
        workouts=[PREV_WORKOUT, CUR_WORKOUT],
    )
    return WhoopService(client=client, oauth=MagicMock())


def _now(iso):
    return patch("whoop.services._utcnow",
                 return_value=datetime.fromisoformat(iso).astimezone(timezone.utc))


class TestDailySummaryLocalDay:
    async def test_sleep_started_before_utc_midnight_is_included(self, service):
        """Oct 26 (local) must include the night whose onset was 23:55 UTC on Oct 25."""
        summary = await service.get_daily_summary(date="2026-10-26")
        assert summary.date == "2026-10-26"
        assert summary.recovery_score == 77
        assert summary.sleep_score == 88
        assert summary.strain == 11.0

    async def test_previous_day_is_not_leaked(self, service):
        summary = await service.get_daily_summary(date="2026-10-25")
        assert summary.recovery_score == 40
        assert summary.sleep_score == 60
        assert summary.strain == 9.0

    async def test_workouts_are_limited_to_the_days_cycle(self, service):
        health = await service._get_daily_health("2026-10-26")
        assert [w.strain for w in health.activity.workouts] == [8.0]

    async def test_today_uses_local_date_after_local_midnight(self, service):
        """01:30 local on Oct 27 is still 2026-10-26 in UTC; 'today' must be Oct 27."""
        with _now("2026-10-26T22:30:00+00:00"):
            summary = await service.get_daily_summary(date=None)
        assert summary.date == "2026-10-27"
        # Oct 27's cycle has not started yet: nothing from Oct 26 may leak in
        assert summary.recovery_score is None

    async def test_today_in_the_morning(self, service):
        with _now("2026-10-26T07:00:00+00:00"):  # 10:00 local
            summary = await service.get_daily_summary(date=None)
        assert summary.date == "2026-10-26"
        assert summary.recovery_score == 77

    async def test_onset_before_local_midnight_belongs_to_wake_day(self):
        """Falling asleep at 23:30 local on Oct 25 starts the Oct 26 cycle."""
        cycle = _cycle(5, "2026-10-25T20:30:00.000Z", None, 7.0)
        sleep = _sleep(55, 5, "2026-10-25T20:30:00.000Z", "2026-10-26T04:00:00.000Z", 81)
        recovery = _recovery(5, 55, "2026-10-26T04:05:00.000Z", 66)
        client = _fake_client([cycle], [sleep], [recovery], [])
        service = WhoopService(client=client, oauth=MagicMock())

        summary = await service.get_daily_summary(date="2026-10-26")
        assert summary.recovery_score == 66
        assert summary.sleep_score == 81


class TestBatchLocalDay:
    async def test_trends_label_cycles_by_local_wake_day(self, service):
        with _now("2026-10-26T07:00:00+00:00"):
            records = await service._fetch_daily_records_batch(days=3)
        by_date = {r.date: r for r in records}
        assert set(by_date) == {"2026-10-25", "2026-10-26"}
        assert by_date["2026-10-26"].recovery.score == 77
        assert by_date["2026-10-26"].sleep.score == 88
        assert by_date["2026-10-25"].recovery.score == 40
        assert by_date["2026-10-25"].sleep.score == 60


class TestDayHelpers:
    @pytest.mark.parametrize("offset, expected", [
        ("+03:00", "2026-10-26"),  # 02:55 local
        ("Z", "2026-10-26"),       # 23:55 UTC -> after noon -> next day
        (None, "2026-10-26"),      # missing offset treated as UTC: 23:55 -> next day
        ("-05:00", "2026-10-26"),  # 18:55 local -> evening onset -> next day
        ("-10:00", "2026-10-26"),  # 13:55 local -> after noon -> next day
        ("+09:00", "2026-10-26"),  # 08:55 local Oct 26 -> same day
        ("garbage", "2026-10-26"), # invalid -> UTC
    ])
    async def test_cycle_day(self, offset, expected):
        from whoop.services import _cycle_day
        cycle = {"start": "2026-10-25T23:55:59.000Z", "timezone_offset": offset}
        assert _cycle_day(cycle) == expected

    async def test_cycle_without_start(self):
        from whoop.services import _cycle_day
        assert _cycle_day({"timezone_offset": TZ}) is None

    async def test_invalid_date_rejected(self, service):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            await service.get_daily_summary(date="26.10.2026")


class TestParseTimestamp:
    @pytest.mark.parametrize("value", [
        "2026-10-25T23:55:59Z",
        "2026-10-25T23:55:59.3Z",
        "2026-10-25T23:55:59.37Z",
        "2026-10-25T23:55:59.370Z",
        "2026-10-25T23:55:59.3701234Z",
        "2026-10-26T02:55:59.370+03:00",
    ])
    async def test_fraction_lengths_parse_to_same_second(self, value):
        from whoop.services import _parse_ts
        ts = _parse_ts(value)
        assert ts is not None
        assert ts.astimezone(timezone.utc).replace(microsecond=0).isoformat() == "2026-10-25T23:55:59+00:00"

    @pytest.mark.parametrize("value", [None, "", "not-a-date"])
    async def test_invalid_returns_none(self, value):
        from whoop.services import _parse_ts
        assert _parse_ts(value) is None
