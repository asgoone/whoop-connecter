"""
Integration tests for WhoopService — API client mocked.
Tests orchestration: data fetching, schema mapping, analytics.
Uses the real WHOOP v2 record shape (tests.conftest *_v2 builders).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from tests.conftest import make_cycle_v2, make_recovery_v2, make_sleep_v2, make_workout_v2
from whoop.api import endpoints
from whoop.errors import ErrorCode, WhoopError
from whoop.services import WhoopService
from whoop.api.client import WhoopClient



def _cycle_id(date):
    return int(date.replace("-", ""))


def _make_recovery_raw(score=70, hrv=55.0, rhr=58, date="2026-03-17"):
    return make_recovery_v2(cycle_id=_cycle_id(date), created_at=f"{date}T06:00:00.000Z",
                            score=score, hrv=hrv, rhr=rhr)


def _make_sleep_raw(score=82, score_state="SCORED",
                    created_at="2026-03-17T00:00:00Z"):
    date = created_at[:10]
    return make_sleep_v2(cycle_id=_cycle_id(date), start=f"{date}T00:00:00.000Z",
                         end=f"{date}T07:30:00.000Z", created_at=created_at,
                         score=score, score_state=score_state)


def _make_cycle_raw(date="2026-03-17", strain=10.5):
    return make_cycle_v2(cycle_id=_cycle_id(date), start=f"{date}T00:20:00.000Z",
                         end=f"{date}T23:40:00.000Z", strain=strain)


def _make_workout_raw():
    return make_workout_v2()


@pytest.fixture
def mock_client():
    client = MagicMock(spec=WhoopClient)
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def mock_oauth():
    oauth = MagicMock()
    oauth.get_access_token.return_value = "token"
    oauth.token_status.return_value = {"authenticated": True, "expires_at": "...", "expired": False}
    return oauth


@pytest.fixture
def service(mock_client, mock_oauth):
    return WhoopService(client=mock_client, oauth=mock_oauth)


class TestGetRecovery:
    async def test_returns_first_record(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={
            "records": [_make_recovery_raw(score=74), _make_recovery_raw(score=60)]
        })
        result = await service.get_recovery()
        assert result["score"]["recovery_score"] == 74

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
        call_kwargs = mock_client.get.call_args.kwargs
        assert call_kwargs.get("params") is None


class TestGetSleep:
    async def test_returns_first_scored_record(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={
            "records": [
                _make_sleep_raw(score=82, score_state="SCORED"),
            ]
        })
        result = await service.get_sleep()
        assert result["score"]["sleep_performance_percentage"] == 82

    async def test_prefers_scored_over_pending(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={
            "records": [
                _make_sleep_raw(score=40, score_state="PENDING_SCORE"),
                _make_sleep_raw(score=82, score_state="SCORED"),
            ]
        })
        result = await service.get_sleep()
        assert result["score"]["sleep_performance_percentage"] == 82

    async def test_empty_records_returns_none(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={"records": []})
        result = await service.get_sleep()
        assert result is None


class TestGetDailySummary:
    async def test_full_data_summary(self, service, mock_client):
        mock_client.get_paginated = AsyncMock(side_effect=[
            [_make_recovery_raw(score=74)],
            [_make_sleep_raw(score=82)],
            [_make_workout_raw()],
            [_make_cycle_raw()],
        ])
        summary = await service.get_daily_summary(date="2026-03-17")
        assert summary.recovery_score == 74
        assert summary.sleep_score == 82
        assert summary.emoji == "🟢"

    async def test_no_data_summary(self, service, mock_client):
        mock_client.get_paginated = AsyncMock(return_value=[])
        summary = await service.get_daily_summary(date="2026-03-17")
        assert summary.recovery_score is None
        assert summary.emoji == "⚪"

class TestGetTrends:
    async def _make_trends_data(self, mock_client, n_days=7, base_score=70):
        cycles = [_make_cycle_raw(date=f"2026-03-{10+i:02d}") for i in range(n_days)]
        recoveries = [
            _make_recovery_raw(score=base_score + i, date=f"2026-03-{10+i:02d}")
            for i in range(n_days)
        ]
        sleeps = [
            {**_make_sleep_raw(score=80),
             "created_at": f"2026-03-{10+i:02d}T00:00:00Z"}
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
        await service.get_trends(days=1)
        mock_client.get_paginated = AsyncMock(return_value=[])
        await service.get_trends(days=90)

    async def test_returns_trend_report(self, service, mock_client):
        await self._make_trends_data(mock_client, n_days=7)
        report = await service.get_trends(days=7)
        assert report.days == 7
        assert len(report.metrics) == 5

    async def test_three_parallel_requests(self, service, mock_client):
        """get_trends should make exactly 3 get_paginated calls."""
        mock_client.get_paginated = AsyncMock(return_value=[])
        await service.get_trends(days=7)
        assert mock_client.get_paginated.call_count == 3

    async def test_cycles_without_matching_recovery(self, service, mock_client):
        cycles = [_make_cycle_raw(date="2026-03-17")]
        mock_client.get_paginated = AsyncMock(side_effect=[cycles, [], []])
        report = await service.get_trends(days=1)
        assert report.days == 1
        rec_trend = next(m for m in report.metrics if m.metric == "recovery_score")
        assert rec_trend.direction == "N/A"


class TestGetBodyMeasurement:
    async def test_returns_raw_dict(self, service, mock_client):
        body_data = {"height_meter": 1.80, "weight_kilogram": 82.5, "max_heart_rate": 195}
        mock_client.get = AsyncMock(return_value=body_data)
        result = await service.get_body_measurement()
        assert result == body_data

    async def test_calls_correct_endpoint(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={})
        await service.get_body_measurement()
        from whoop.api import endpoints
        mock_client.get.assert_called_once_with(endpoints.BODY_MEASUREMENT)


class TestGetExport:
    async def test_returns_export_dict(self, service, mock_client):
        body_data = {"height_meter": 1.80, "weight_kilogram": 82.5, "max_heart_rate": 195}
        mock_client.get = AsyncMock(return_value=body_data)

        cycles = [_make_cycle_raw(date="2026-03-17")]
        recoveries = [_make_recovery_raw(score=74)]
        sleeps = [{**_make_sleep_raw(score=82), "created_at": "2026-03-17T00:00:00Z"}]
        mock_client.get_paginated = AsyncMock(side_effect=[cycles, recoveries, sleeps])

        result = await service.get_export(days=1)
        assert "body" in result
        assert "daily" in result
        assert result["days"] == 1
        assert result["body"]["height_meter"] == 1.80
        assert len(result["daily"]) == 1

    async def test_days_validation(self, service, mock_client):
        with pytest.raises(ValueError, match="days must be between"):
            await service.get_export(days=0)
        with pytest.raises(ValueError, match="days must be between"):
            await service.get_export(days=91)

    async def test_uses_batch_requests(self, service, mock_client):
        """get_export should use batch (get + get_paginated), not per-day calls."""
        mock_client.get = AsyncMock(return_value={})
        mock_client.get_paginated = AsyncMock(return_value=[])
        await service.get_export(days=7)
        # 1 get (body) + 3 get_paginated (cycles, recovery, sleep)
        assert mock_client.get.call_count == 1
        assert mock_client.get_paginated.call_count == 3


class TestAuthStatus:
    def test_delegates_to_oauth(self, service, mock_oauth):
        result = service.auth_status()
        mock_oauth.token_status.assert_called_once()
        assert result["authenticated"] is True

    def test_not_authenticated(self, service, mock_oauth):
        mock_oauth.token_status.return_value = {"authenticated": False, "expires_at": None}
        result = service.auth_status()
        assert result["authenticated"] is False

    async def test_auth_check_ok_never_returns_profile(self, service, mock_client):
        mock_client.get = AsyncMock(return_value={"user_id": 1, "email": "owner@example.com"})
        result = await service.auth_check()
        assert result["check"] == {"ok": True}
        assert "owner@example.com" not in str(result)
        mock_client.get.assert_awaited_once_with(endpoints.PROFILE, use_cache=False)

    async def test_auth_check_reports_typed_error(self, service, mock_client):
        mock_client.get = AsyncMock(side_effect=WhoopError(ErrorCode.REAUTH_REQUIRED, "rejected"))
        result = await service.auth_check()
        assert result["check"]["ok"] is False
        assert result["check"]["code"] == "REAUTH_REQUIRED"
        assert result["check"]["hint"]
        assert result["authenticated"] is True  # passive status is still included

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
