"""
Integration tests for MCP server and tool handlers.
Verifies tool registration, routing, JSON output, and error propagation.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest
from whoop.api.client import WhoopAPIError
from whoop.analytics.daily_summary import DailySummary
from whoop.analytics.trends import TrendReport, MetricTrend


pytestmark = pytest.mark.asyncio

EXPECTED_TOOLS = {
    "get_auth_status",
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


async def _call(server, tool_name: str, arguments: dict = None) -> str:
    handler = server.request_handlers[CallToolRequest]
    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=tool_name, arguments=arguments or {}),
    )
    result = await handler(req)
    return result.root.content[0].text


class TestToolRegistration:
    async def test_all_tools_registered(self, server):
        handler = server.request_handlers[ListToolsRequest]
        result = await handler(ListToolsRequest(method="tools/list"))
        registered = {t.name for t in result.root.tools}
        assert registered == EXPECTED_TOOLS

    async def test_unknown_tool_returns_error_text(self, server):
        text = await _call(server, "nonexistent_tool")
        assert "Unknown tool" in text

    async def test_tool_count_is_eight(self, server):
        handler = server.request_handlers[ListToolsRequest]
        result = await handler(ListToolsRequest(method="tools/list"))
        assert len(result.root.tools) == 8


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


class TestRecoveryTool:
    async def test_no_data_returns_error_key(self, server, mock_service):
        mock_service.get_recovery = AsyncMock(return_value=None)
        text = await _call(server, "get_recovery")
        data = json.loads(text)
        assert "error" in data

    async def test_with_flat_data_returns_score(self, server, mock_service):
        """Test with real flat API format."""
        raw = {
            "score_state": "SCORED",
            "recovery_score": 74,
            "hrv_rmssd_milli": 55.0,
            "resting_heart_rate": 58,
            "spo2_percentage": 97.0,
            "skin_temp_celsius": 0.0,
        }
        mock_service.get_recovery = AsyncMock(return_value=raw)
        text = await _call(server, "get_recovery")
        data = json.loads(text)
        assert data["score"] == 74

    async def test_date_params_passed_through(self, server, mock_service):
        mock_service.get_recovery = AsyncMock(return_value=None)
        await _call(server, "get_recovery", {"start": "2026-03-17T00:00:00Z"})
        mock_service.get_recovery.assert_called_once_with(start="2026-03-17T00:00:00Z", end=None)


class TestSleepTool:
    async def test_no_data_returns_error_key(self, server, mock_service):
        mock_service.get_sleep = AsyncMock(return_value=None)
        text = await _call(server, "get_sleep")
        data = json.loads(text)
        assert "error" in data

    async def test_with_flat_data_returns_score(self, server, mock_service):
        """Test with real flat API format."""
        raw = {
            "score_state": "SCORED",
            "sleep_performance_percentage": 85,
            "sleep_efficiency_percentage": 91.0,
            "total_in_bed_time_milli": 27000000,
            "total_light_sleep_time_milli": 13000000,
        }
        mock_service.get_sleep = AsyncMock(return_value=raw)
        text = await _call(server, "get_sleep")
        data = json.loads(text)
        assert data["score"] == 85


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

    async def test_invalid_days_returns_error(self, server, mock_service):
        mock_service.get_trends = AsyncMock(side_effect=ValueError("days must be between 1 and 90"))
        text = await _call(server, "get_trends", {"days": 0})
        data = json.loads(text)
        assert "error" in data

    async def test_default_days_is_7(self, server, mock_service):
        await _call(server, "get_trends", {})
        mock_service.get_trends.assert_called_once_with(days=7)


class TestErrorHandling:
    async def test_api_error_returns_specific_message(self, server, mock_service):
        mock_service.get_recovery = AsyncMock(
            side_effect=WhoopAPIError(429, "Rate limited")
        )
        text = await _call(server, "get_recovery")
        assert "429" in text or "Rate limited" in text

    async def test_unexpected_error_returns_internal_error(self, server, mock_service):
        mock_service.get_profile = AsyncMock(side_effect=RuntimeError("Unexpected!"))
        text = await _call(server, "get_profile")
        assert "Internal error" in text or "Unexpected" in text

    async def test_value_error_returns_invalid_arguments(self, server, mock_service):
        mock_service.get_trends = AsyncMock(side_effect=ValueError("bad input"))
        text = await _call(server, "get_trends", {"days": -1})
        assert "Invalid arguments" in text or "bad input" in text
