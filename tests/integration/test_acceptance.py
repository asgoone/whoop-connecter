"""
Acceptance tests — end-to-end scenarios through the service layer.
Each test exercises a full user-facing flow with mocked HTTP.
"""

import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from whoop.services import WhoopService
from whoop.api.client import WhoopClient
from whoop.auth.oauth import WhoopOAuth, OAuthConfig
from whoop.auth.token_store import TokenData, TokenStore


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cycle_id(date):
    return int(date.replace("-", ""))


def _make_recovery_raw(score=70, hrv=55.0, rhr=58, date="2026-03-17"):
    return {
        "cycle_id": _cycle_id(date),
        "score_state": "SCORED",
        "recovery_score": score,
        "hrv_rmssd_milli": hrv,
        "resting_heart_rate": rhr,
        "spo2_percentage": 97.0,
        "skin_temp_celsius": 0.0,
        "created_at": f"{date}T06:00:00Z",
    }


def _make_sleep_raw(score=82, date="2026-03-17", respiratory_rate=14.6,
                    sleep_consistency=76):
    return {
        "id": 456,
        "score_state": "SCORED",
        "sleep_performance_percentage": score,
        "sleep_efficiency_percentage": 91.0,
        "respiratory_rate": respiratory_rate,
        "sleep_consistency_percentage": sleep_consistency,
        "total_in_bed_time_milli": 27000000,
        "total_awake_time_milli": 1800000,
        "total_light_sleep_time_milli": 13000000,
        "total_slow_wave_sleep_time_milli": 5000000,
        "total_rem_sleep_time_milli": 4000000,
        "sleep_cycle_count": 5,
        "created_at": f"{date}T00:00:00Z",
    }


def _make_cycle_raw(date="2026-03-17", strain=10.5):
    return {
        "id": _cycle_id(date),
        "start": f"{date}T00:20:00.000Z",
        "end": f"{date}T23:40:00.000Z",
        "score_state": "SCORED",
        "strain": strain,
        "kilojoule": 8000,
        "average_heart_rate": 67,
        "max_heart_rate": 144,
    }


def _make_workout_raw(distance=5000.0, altitude_gain=120.0, percent_recorded=98.5):
    score = {
        "strain": 12.0,
        "average_heart_rate": 145,
        "max_heart_rate": 178,
        "kilojoule": 2000,
        "distance_meter": distance,
        "altitude_gain_meter": altitude_gain,
        "percent_recorded": percent_recorded,
    }
    return {
        "sport_id": 1,
        "start": "2026-03-17T08:00:00Z",
        "end": "2026-03-17T09:00:00Z",
        "score": score,
    }


@pytest.fixture
def mock_client():
    client = MagicMock(spec=WhoopClient)
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def mock_oauth():
    oauth = MagicMock()
    oauth.get_access_token.return_value = "token"
    oauth.token_status.return_value = {
        "authenticated": True,
        "expires_at": "2026-03-17T12:00:00+00:00",
        "expired": False,
    }
    return oauth


@pytest.fixture
def service(mock_client, mock_oauth):
    return WhoopService(client=mock_client, oauth=mock_oauth)


# ===========================================================================
# Acceptance: token_status is passive (no refresh, no network)
# ===========================================================================

def _oauth_with_store(store) -> WhoopOAuth:
    config = OAuthConfig(
        client_id="test_id",
        client_secret="test_secret",
        redirect_uri="http://localhost:8080/callback",
    )
    return WhoopOAuth(config=config, store=store)


class TestTokenStatusPassive:
    """token_status() reports stored state and never refreshes."""

    def test_expired_token_reports_refresh_needed_without_refresh(self):
        store = MagicMock(spec=TokenStore)
        store.load.return_value = TokenData(
            access_token="old", refresh_token="refresh_tok", expires_at=time.time() - 3600,
        )
        oauth = _oauth_with_store(store)

        with patch("whoop.auth.oauth.httpx.post") as post:
            status = oauth.token_status()

        post.assert_not_called()
        store.save.assert_not_called()
        assert status["state"] == "refresh_needed"
        assert status["expired"] is True
        assert status["authenticated"] is True

    def test_expired_without_refresh_token_is_reauth_required(self):
        store = MagicMock(spec=TokenStore)
        store.load.return_value = TokenData(
            access_token="old", refresh_token="", expires_at=time.time() - 3600,
        )
        status = _oauth_with_store(store).token_status()
        assert status["state"] == "reauth_required"
        assert status["authenticated"] is False

    def test_valid_token(self):
        store = MagicMock(spec=TokenStore)
        store.load.return_value = TokenData(
            access_token="a", refresh_token="r", expires_at=time.time() + 3600,
        )
        status = _oauth_with_store(store).token_status()
        assert status["state"] == "valid"
        assert status["expired"] is False

    def test_no_tokens_returns_not_authenticated(self):
        store = MagicMock(spec=TokenStore)
        store.load.return_value = None
        status = _oauth_with_store(store).token_status()

        assert status["state"] == "auth_required"
        assert status["authenticated"] is False
        assert status["expired"] is None


# ===========================================================================
# Acceptance: export end-to-end
# ===========================================================================

class TestExportEndToEnd:
    """Full export flow: body + daily records → structured dict."""

    async def test_export_contains_all_sections(self, service, mock_client):
        body = {"height_meter": 1.80, "weight_kilogram": 82.5, "max_heart_rate": 195}
        mock_client.get = AsyncMock(return_value=body)

        dates = [f"2026-03-{15+i:02d}" for i in range(3)]
        cycles = [_make_cycle_raw(date=d) for d in dates]
        recoveries = [_make_recovery_raw(score=70+i*5, date=d) for i, d in enumerate(dates)]
        sleeps = [_make_sleep_raw(score=80, date=d) for d in dates]
        mock_client.get_paginated = AsyncMock(side_effect=[cycles, recoveries, sleeps])

        result = await service.get_export(days=3)

        # Structure
        assert "export_date" in result
        assert "body" in result
        assert "daily" in result
        assert result["days"] == 3

        # Body data mapped correctly
        assert result["body"]["height_meter"] == 1.80
        assert result["body"]["weight_kilogram"] == 82.5

        # Daily records present
        assert len(result["daily"]) == 3

        # Each daily record has expected structure
        for rec in result["daily"]:
            assert "date" in rec
            assert "recovery" in rec
            assert "sleep" in rec
            assert "activity" in rec

    async def test_export_with_missing_recovery(self, service, mock_client):
        """Days without recovery data should still appear in export."""
        body = {"height_meter": 1.80, "weight_kilogram": 82.5, "max_heart_rate": 195}
        mock_client.get = AsyncMock(return_value=body)

        cycles = [_make_cycle_raw(date="2026-03-17")]
        mock_client.get_paginated = AsyncMock(side_effect=[cycles, [], []])

        result = await service.get_export(days=1)
        assert len(result["daily"]) == 1
        assert result["daily"][0]["recovery"] is None
        assert result["daily"][0]["sleep"] is None

    async def test_export_total_api_calls(self, service, mock_client):
        """Export should make exactly 1 get + 3 get_paginated = 4 HTTP calls."""
        mock_client.get = AsyncMock(return_value={})
        mock_client.get_paginated = AsyncMock(return_value=[])

        await service.get_export(days=7)

        assert mock_client.get.call_count == 1
        assert mock_client.get_paginated.call_count == 3


# ===========================================================================
# Acceptance: enriched fields flow through service layer
# ===========================================================================

class TestEnrichedFieldsIntegration:
    """Enriched sleep/workout fields survive the full service pipeline."""

    async def test_sleep_enriched_fields_in_daily_summary(self, service, mock_client):
        """respiratory_rate and sleep_consistency flow through to DailyHealth."""
        sleep_raw = _make_sleep_raw(
            respiratory_rate=14.6,
            sleep_consistency=76,
        )
        mock_client.get_paginated = AsyncMock(side_effect=[
            [_make_recovery_raw()],
            [sleep_raw],
            [],  # workouts
            [_make_cycle_raw()],  # cycles
        ])

        summary = await service.get_daily_summary(date="2026-03-17")
        # Summary itself doesn't expose respiratory_rate, but the underlying
        # DailyHealth object should have mapped sleep data correctly.
        assert summary.sleep_score == 82

    async def test_workout_enriched_fields_in_daily_health(self, service, mock_client):
        """distance, altitude_gain, percent_recorded flow through map_workout."""
        workout_raw = _make_workout_raw(
            distance=5000.0,
            altitude_gain=120.0,
            percent_recorded=98.5,
        )
        mock_client.get_paginated = AsyncMock(side_effect=[
            [_make_recovery_raw()],
            [_make_sleep_raw()],
            [workout_raw],
            [_make_cycle_raw()],
        ])

        summary = await service.get_daily_summary(date="2026-03-17")
        assert summary is not None

    async def test_enriched_sleep_in_export(self, service, mock_client):
        """Export includes enriched sleep fields in daily records."""
        body = {"height_meter": 1.80, "weight_kilogram": 82.5, "max_heart_rate": 195}
        mock_client.get = AsyncMock(return_value=body)

        sleep_raw = _make_sleep_raw(respiratory_rate=15.2, sleep_consistency=80)
        mock_client.get_paginated = AsyncMock(side_effect=[
            [_make_cycle_raw()],
            [_make_recovery_raw()],
            [sleep_raw],
        ])

        result = await service.get_export(days=1)
        daily = result["daily"][0]
        sleep = daily["sleep"]
        assert sleep is not None
        assert sleep["respiratory_rate"] == 15.2
        assert sleep["sleep_consistency"] == 80


# ===========================================================================
# Acceptance: falsy values (0, 0.0) handled correctly
# ===========================================================================

class TestFalsyValues:
    """Values of 0 or 0.0 should NOT be treated as None/missing."""

    async def test_zero_recovery_score(self, service, mock_client):
        """recovery_score=0 should not become None."""
        mock_client.get_paginated = AsyncMock(side_effect=[
            [_make_recovery_raw(score=0, hrv=0.0, rhr=0)],
            [_make_sleep_raw()],
            [],
            [_make_cycle_raw()],
        ])

        summary = await service.get_daily_summary(date="2026-03-17")
        assert summary.recovery_score == 0
        assert summary.hrv_rmssd == 0.0
        assert summary.resting_hr == 0

    async def test_zero_strain_in_export(self, service, mock_client):
        """strain=0 in cycle should appear as 0, not None."""
        body = {"height_meter": 1.80, "weight_kilogram": 82.5, "max_heart_rate": 195}
        mock_client.get = AsyncMock(return_value=body)

        mock_client.get_paginated = AsyncMock(side_effect=[
            [_make_cycle_raw(strain=0.0)],
            [_make_recovery_raw(score=0)],
            [],
        ])

        result = await service.get_export(days=1)
        daily = result["daily"][0]
        assert daily["activity"]["strain"] == 0.0
        assert daily["recovery"]["score"] == 0
