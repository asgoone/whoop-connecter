"""
Integration tests for MCP server and tool handlers.
Verifies tool registration, routing, JSON output, and error propagation.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest
from tests.conftest import make_cycle_v2, make_recovery_v2, make_sleep_v2, make_workout_v2
from whoop.api.client import WhoopAPIError
from whoop.analytics.daily_summary import DailySummary
from whoop.analytics.trends import TrendReport, MetricTrend


EXPECTED_TOOLS = {
    "get_auth_status",
    "get_body_measurement",
    "get_profile",
    "get_recovery",
    "get_sleep",
    "get_workouts",
    "get_cycles",
    "get_daily_summary",
    "get_trends",
}


@pytest.fixture
def mock_service():
    svc = MagicMock()
    svc.auth_status.return_value = {
        "authenticated": True,
        "expires_at": "2026-03-18T08:00:00+00:00",
        "expired": False,
    }
    svc.get_profile = AsyncMock(return_value={"user_id": 1, "email": "test@example.com"})
    svc.get_body_measurement = AsyncMock(return_value={
        "height_meter": 1.80, "weight_kilogram": 82.5, "max_heart_rate": 195,
    })
    svc.get_recovery = AsyncMock(return_value=None)
    svc.get_sleep = AsyncMock(return_value=None)
    svc.get_workouts = AsyncMock(return_value=[])
    svc.get_cycles = AsyncMock(return_value=[])
    svc.get_daily_summary = AsyncMock(return_value=DailySummary(
        date="2026-03-17",
        recovery_score=74,
        sleep_score=82,
        hrv_rmssd=55.2,
        resting_hr=58,
        strain=10.5,
        recommendation="Good recovery. You can train at full intensity.",
        emoji="🟢",
    ))
    svc.get_trends = AsyncMock(return_value=TrendReport(
        days=7,
        from_date="2026-03-10",
        to_date="2026-03-17",
        metrics=[
            MetricTrend("recovery_score", 70.0, 65.0, 75.0, "↑", 15.4),
            MetricTrend("sleep_score", 81.0, 80.0, 82.0, "→", 2.5),
            MetricTrend("hrv_rmssd", 55.0, 53.0, 57.0, "↑", 7.5),
            MetricTrend("resting_hr", 58.0, 59.0, 57.0, "↓", -3.4),
            MetricTrend("strain", 10.2, 9.5, 10.9, "↑", 14.7),
        ],
    ))
    return svc


@pytest.fixture
def server(mock_service):
    from mcp_server.server import create_server
    return create_server(mock_service)


async def _call_result(server, tool_name: str, arguments: dict = None):
    handler = server.request_handlers[CallToolRequest]
    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=tool_name, arguments=arguments or {}),
    )
    return (await handler(req)).root


async def _call(server, tool_name: str, arguments: dict = None) -> str:
    return (await _call_result(server, tool_name, arguments)).content[0].text


class TestToolRegistration:
    async def test_all_tools_registered(self, server):
        handler = server.request_handlers[ListToolsRequest]
        result = await handler(ListToolsRequest(method="tools/list"))
        registered = {t.name for t in result.root.tools}
        assert registered == EXPECTED_TOOLS

    async def test_unknown_tool_returns_error_text(self, server):
        text = await _call(server, "nonexistent_tool")
        assert "Unknown tool" in text

class TestAuthStatusTool:
    async def test_returns_valid_json(self, server, mock_service):
        text = await _call(server, "get_auth_status")
        data = json.loads(text)
        assert data["authenticated"] is True
        assert "expires_at" in data

    async def test_not_authenticated(self, server, mock_service):
        mock_service.auth_status.return_value = {"authenticated": False, "expires_at": None}
        text = await _call(server, "get_auth_status")
        data = json.loads(text)
        assert data["authenticated"] is False


class TestProfileTool:
    async def test_returns_profile_json(self, server, mock_service):
        text = await _call(server, "get_profile")
        data = json.loads(text)
        assert data["email"] == "test@example.com"


class TestBodyMeasurementTool:
    async def test_returns_body_data(self, server):
        text = await _call(server, "get_body_measurement")
        data = json.loads(text)
        assert data["height_meter"] == 1.80
        assert data["weight_kilogram"] == 82.5
        assert data["max_heart_rate"] == 195

    async def test_empty_response_returns_all_none(self, server, mock_service):
        mock_service.get_body_measurement = AsyncMock(return_value={})
        text = await _call(server, "get_body_measurement")
        data = json.loads(text)
        assert data["height_meter"] is None
        assert data["weight_kilogram"] is None
        assert data["max_heart_rate"] is None


class TestRecoveryTool:
    async def test_no_data_is_normal_result(self, server, mock_service):
        mock_service.get_recovery = AsyncMock(return_value=None)
        result = await _call_result(server, "get_recovery")
        data = json.loads(result.content[0].text)
        assert result.isError is False
        assert data["no_data"] is True
        assert "error" not in data

    async def test_v2_record_returns_metrics(self, server, mock_service):
        mock_service.get_recovery = AsyncMock(return_value=make_recovery_v2(score=74, hrv=55.0, rhr=58))
        result = await _call_result(server, "get_recovery")
        data = json.loads(result.content[0].text)
        assert result.isError is False
        assert (data["score"], data["hrv_rmssd"], data["resting_hr"]) == (74, 55.0, 58)

    async def test_date_params_passed_through(self, server, mock_service):
        mock_service.get_recovery = AsyncMock(return_value=None)
        await _call(server, "get_recovery", {"start": "2026-03-17T00:00:00Z"})
        mock_service.get_recovery.assert_called_once_with(start="2026-03-17T00:00:00Z", end=None)


class TestSleepTool:
    async def test_no_data_is_normal_result(self, server, mock_service):
        mock_service.get_sleep = AsyncMock(return_value=None)
        result = await _call_result(server, "get_sleep")
        data = json.loads(result.content[0].text)
        assert result.isError is False
        assert data["no_data"] is True
        assert "error" not in data

    async def test_v2_record_returns_metrics(self, server, mock_service):
        mock_service.get_sleep = AsyncMock(return_value=make_sleep_v2(score=85, in_bed_ms=27_000_000))
        data = json.loads(await _call(server, "get_sleep"))
        assert data["score"] == 85
        assert data["duration_hours"] == 7.5


class TestWorkoutsTool:
    async def test_maps_v2_workouts(self, server, mock_service):
        mock_service.get_workouts = AsyncMock(return_value=[make_workout_v2(sport_name="running")])
        data = json.loads(await _call(server, "get_workouts", {"start": "2026-03-17T00:00:00Z"}))
        assert len(data) == 1
        assert data[0]["sport"] == "running"
        assert data[0]["strain"] == 12.0
        assert data[0]["duration_minutes"] == 60.0
        mock_service.get_workouts.assert_called_once_with(start="2026-03-17T00:00:00Z", end=None)

    async def test_no_workouts_is_empty_list(self, server, mock_service):
        result = await _call_result(server, "get_workouts")
        assert result.isError is False
        assert json.loads(result.content[0].text) == []


class TestCyclesTool:
    async def test_maps_v2_cycles(self, server, mock_service):
        mock_service.get_cycles = AsyncMock(return_value=[make_cycle_v2(strain=13.4, kj=9000.0)])
        data = json.loads(await _call(server, "get_cycles"))
        assert data == [{"strain": 13.4, "calories": int(9000.0 / 4.184)}]


class TestDailySummaryTool:
    async def test_returns_complete_summary(self, server):
        text = await _call(server, "get_daily_summary")
        data = json.loads(text)
        assert data["recovery_score"] == 74
        assert data["sleep_score"] == 82
        assert "summary_line" in data
        assert "🟢" in data["summary_line"]

    async def test_date_param_forwarded(self, server, mock_service):
        await _call(server, "get_daily_summary", {"date": "2026-03-15"})
        mock_service.get_daily_summary.assert_called_once_with(date="2026-03-15")

    async def test_no_date_uses_default(self, server, mock_service):
        await _call(server, "get_daily_summary", {})
        mock_service.get_daily_summary.assert_called_once_with(date=None)


class TestTrendsTool:
    async def test_returns_trend_report(self, server):
        text = await _call(server, "get_trends", {"days": 7})
        data = json.loads(text)
        assert data["days"] == 7
        assert len(data["metrics"]) == 5

    async def test_out_of_range_days_rejected_by_schema(self, server, mock_service):
        result = await _call_result(server, "get_trends", {"days": 500})
        assert result.isError is True
        mock_service.get_trends.assert_not_called()

    async def test_service_value_error_is_invalid_arguments(self, server, mock_service):
        mock_service.get_trends = AsyncMock(side_effect=ValueError("days must be between 1 and 90"))
        result = await _call_result(server, "get_trends", {"days": 7})
        assert result.isError is True
        assert json.loads(result.content[0].text)["error"]["code"] == "INVALID_ARGUMENTS"

    async def test_default_days_is_7(self, server, mock_service):
        await _call(server, "get_trends", {})
        mock_service.get_trends.assert_called_once_with(days=7)


class TestErrorHandling:
    async def test_api_error_returns_specific_message(self, server, mock_service):
        mock_service.get_recovery = AsyncMock(
            side_effect=WhoopAPIError(429, "Rate limited")
        )
        result = await _call_result(server, "get_recovery")
        assert result.isError is True
        error = json.loads(result.content[0].text)["error"]
        assert error["code"] == "RATE_LIMITED"
        assert error["hint"]

    async def test_unexpected_error_returns_internal_error_without_details(self, server, mock_service):
        mock_service.get_profile = AsyncMock(side_effect=RuntimeError("secret detail"))
        text = await _call(server, "get_profile")
        data = json.loads(text)
        assert data["error"]["code"] == "INTERNAL_ERROR"
        assert "secret detail" not in text

    async def test_value_error_returns_invalid_arguments(self, server, mock_service):
        mock_service.get_trends = AsyncMock(side_effect=ValueError("bad input"))
        result = await _call_result(server, "get_trends", {"days": 7})
        assert result.isError is True
        error = json.loads(result.content[0].text)["error"]
        assert error["code"] == "INVALID_ARGUMENTS"
        assert error["message"] == "bad input"
