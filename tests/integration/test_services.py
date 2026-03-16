"""
Integration tests for WhoopService — API client mocked.
Tests orchestration: data fetching, schema mapping, analytics.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from whoop.services import WhoopService
from whoop.api.client import WhoopClient


pytestmark = pytest.mark.asyncio


def _make_recovery_raw(score=70.0, hrv=55.0, rhr=58):
    return {"score": {"recovery_score": score, "hrv_rmssd_milli": hrv, "resting_heart_rate": rhr}}


def _make_sleep_raw(score=82, nap=False, start="2026-03-17T00:00:00Z", end="2026-03-17T07:30:00Z"):
    return {"nap": nap, "start": start, "end": end,
            "score": {"sleep_performance_percentage": score, "sleep_efficiency_percentage": 91.0}}


def _make_cycle_raw(date="2026-03-17", strain=10.5):
    return {"start": f"{date}T06:00:00Z", "score": {"strain": strain, "kilojoule": 8000}}


def _make_workout_raw():
    return {"sport_id": 0, "start": "2026-03-17T08:00:00Z", "end": "2026-03-17T09:00:00Z",
            "score": {"strain": 12.0, "average_heart_rate": 145, "max_heart_rate": 178, "kilojoule": 2000}}


@pytest.fixture
def mock_client():
    client = MagicMock(spec=WhoopClient)
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def mock_oauth():
    oauth = MagicMock()
    oauth.ensure_valid_token.return_value = "token"
    oauth.token_status.return_value = {"authenticated": True, "expires_at": "...", "expired": False}
    return oauth


@pytest.fixture
def service(mock_client, mock_oauth):
    return WhoopService(client=mock_client, oauth=mock_oauth)


class TestGetRecovery:
    async def test_returns_first_record(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={
            "records": [_make_recovery_raw(score=74.0), _make_recovery_raw(score=60.0)]
        })
        result = await service.get_recovery()
        assert result["score"]["recovery_score"] == 74.0

    async def test_empty_records_returns_none(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={"records": []})
        result = await service.get_recovery()
        assert result is None

    async def test_date_params_passed(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={"records": []})
        await service.get_recovery(start="2026-03-17T00:00:00Z", end="2026-03-17T23:59:59Z")
        params = mock_client.get.call_args[1].get("params") or mock_client.get.call_args[0][1]
        assert params["start"] == "2026-03-17T00:00:00Z"

    async def test_no_date_params_sends_none(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={"records": []})
        await service.get_recovery()
        # When both start and end are None, _date_params returns None → get is called with params=None
        call_kwargs = mock_client.get.call_args.kwargs
        assert call_kwargs.get("params") is None


class TestGetSleep:
    async def test_returns_main_sleep_not_nap(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={
            "records": [
                _make_sleep_raw(score=40, nap=True),   # нужно пропустить
                _make_sleep_raw(score=82, nap=False),  # взять этот
            ]
        })
        result = await service.get_sleep()
        assert result["score"]["sleep_performance_percentage"] == 82

    async def test_all_naps_returns_first(self, service, mock_client):
        """Если все — nap, возвращаем первый (не None)."""
        mock_client.get = AsyncMock(return_value={
            "records": [_make_sleep_raw(score=50, nap=True)]
        })
        result = await service.get_sleep()
        assert result is not None

    async def test_empty_records_returns_none(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={"records": []})
        result = await service.get_sleep()
        assert result is None


class TestGetDailySummary:
    async def test_full_data_summary(self, service, mock_client):
        mock_client.get = AsyncMock(side_effect=[
            {"records": [_make_recovery_raw(score=74.0)]},   # recovery
            {"records": [_make_sleep_raw(score=82)]},        # sleep
        ])
        mock_client.get_paginated = AsyncMock(side_effect=[
            [_make_workout_raw()],   # workouts
            [_make_cycle_raw()],     # cycles
        ])
        summary = await service.get_daily_summary(date="2026-03-17")
        assert summary.recovery_score == 74
        assert summary.sleep_score == 82
        assert summary.emoji == "🟢"

    async def test_no_data_summary(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={"records": []})
        mock_client.get_paginated = AsyncMock(return_value=[])
        summary = await service.get_daily_summary(date="2026-03-17")
        assert summary.recovery_score is None
        assert summary.emoji == "⚪"

    async def test_date_defaults_to_today(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={"records": []})
        mock_client.get_paginated = AsyncMock(return_value=[])
        # Не должно бросить исключение
        summary = await service.get_daily_summary(date=None)
        assert summary is not None


class TestGetTrends:
    async def _make_trends_data(self, mock_client, n_days=7, base_score=70):
        cycles = [_make_cycle_raw(date=f"2026-03-{10+i:02d}") for i in range(n_days)]
        recoveries = [
            {**_make_recovery_raw(score=base_score + i),
             "start": f"2026-03-{10+i:02d}T06:00:00Z"}
            for i in range(n_days)
        ]
        sleeps = [
            {**_make_sleep_raw(score=80), "start": f"2026-03-{10+i:02d}T00:00:00Z"}
            for i in range(n_days)
        ]
        mock_client.get_paginated = AsyncMock(side_effect=[cycles, recoveries, sleeps])

    async def test_days_validation_below_min(self, service, mock_client):
        with pytest.raises(ValueError, match="days must be between"):
            await service.get_trends(days=0)

    async def test_days_validation_above_max(self, service, mock_client):
        with pytest.raises(ValueError, match="days must be between"):
            await service.get_trends(days=91)

    async def test_days_boundary_values_accepted(self, service, mock_client):
        mock_client.get_paginated = AsyncMock(return_value=[])
        await service.get_trends(days=1)   # min
        mock_client.get_paginated = AsyncMock(return_value=[])
        await service.get_trends(days=90)  # max

    async def test_returns_trend_report(self, service, mock_client):
        await self._make_trends_data(mock_client, n_days=7)
        report = await service.get_trends(days=7)
        assert report.days == 7
        assert len(report.metrics) == 5

    async def test_three_parallel_requests(self, service, mock_client):
        """get_trends должен делать ровно 3 вызова get_paginated."""
        mock_client.get_paginated = AsyncMock(return_value=[])
        await service.get_trends(days=7)
        assert mock_client.get_paginated.call_count == 3

    async def test_cycles_without_matching_recovery(self, service, mock_client):
        """Цикл без соответствующего recovery — recovery=None, не падает."""
        cycles = [_make_cycle_raw(date="2026-03-17")]
        mock_client.get_paginated = AsyncMock(side_effect=[cycles, [], []])
        report = await service.get_trends(days=1)
        assert report.days == 1
        rec_trend = next(m for m in report.metrics if m.metric == "recovery_score")
        assert rec_trend.direction == "N/A"

    async def test_sleep_naps_excluded_from_lookup(self, service, mock_client):
        cycles = [_make_cycle_raw(date="2026-03-17")]
        recoveries = [{**_make_recovery_raw(), "start": "2026-03-17T06:00:00Z"}]
        sleeps = [_make_sleep_raw(nap=True, start="2026-03-17T14:00:00Z")]  # только nap
        mock_client.get_paginated = AsyncMock(side_effect=[cycles, recoveries, sleeps])
        report = await service.get_trends(days=1)
        sleep_trend = next(m for m in report.metrics if m.metric == "sleep_score")
        # Nap исключён → нет sleep данных → N/A
        assert sleep_trend.direction == "N/A"


class TestAuthStatus:
    def test_delegates_to_oauth(self, service, mock_oauth):
        result = service.auth_status()
        mock_oauth.token_status.assert_called_once()
        assert result["authenticated"] is True

    def test_not_authenticated(self, service, mock_oauth):
        mock_oauth.token_status.return_value = {"authenticated": False, "expires_at": None}
        result = service.auth_status()
        assert result["authenticated"] is False

    def test_logout_calls_revoke(self, service, mock_oauth):
        service.logout()
        mock_oauth.revoke.assert_called_once()

    async def test_aclose_calls_client_aclose(self, service, mock_client):
        await service.aclose()
        mock_client.aclose.assert_called_once()


class TestDateParams:
    def test_both_none_returns_none(self, service):
        assert service._date_params(None, None) is None

    def test_both_provided(self, service):
        p = service._date_params("2026-03-10T00:00:00Z", "2026-03-17T23:59:59Z")
        assert p == {"start": "2026-03-10T00:00:00Z", "end": "2026-03-17T23:59:59Z"}

    def test_only_start(self, service):
        p = service._date_params("2026-03-10T00:00:00Z", None)
        assert "start" in p
        assert "end" not in p

    def test_only_end(self, service):
        p = service._date_params(None, "2026-03-17T23:59:59Z")
        assert "end" in p
        assert "start" not in p
