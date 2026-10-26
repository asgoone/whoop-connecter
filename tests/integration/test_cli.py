"""
CLI exit codes and passive auth status. No network: tokens are absent,
damaged, or the token endpoint is mocked.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from cli.main import app
from tests.conftest import make_body_measurement, make_recovery_v2, make_sleep_v2
from whoop.analytics.daily_summary import DailySummary
from whoop.analytics.trends import MetricTrend, TrendReport
from whoop.auth.oauth import WHOOP_TOKEN_URL
from whoop.auth.token_store import TokenData, TokenStore

KEY_HEX = "a" * 64
runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOP_CLIENT_ID", "cid")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("WHOOP_TOKEN_ENCRYPTION_KEY", KEY_HEX)
    monkeypatch.setenv("WHOOP_TOKEN_PATH", str(tmp_path / "tokens.enc"))
    return tmp_path / "tokens.enc"


def test_status_without_tokens_exits_3(env):
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 3
    assert "auth_required" in result.output


def test_status_with_damaged_file_exits_5_and_keeps_file(env):
    env.write_bytes(b"x" * 40)
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 5
    assert env.read_bytes() == b"x" * 40


def test_status_expired_does_not_refresh(env):
    TokenStore(str(env)).save(TokenData("a", "r", time.time() - 100))
    with patch("whoop.auth.oauth.httpx.post") as post:
        result = runner.invoke(app, ["auth", "status"])
    post.assert_not_called()
    assert result.exit_code == 0
    assert "refresh_needed" in result.output


def test_data_command_with_revoked_refresh_exits_3(env):
    TokenStore(str(env)).save(TokenData("a", "r", time.time() - 100))
    bad = httpx.Response(400, json={"error": "invalid_grant"}, request=httpx.Request("POST", WHOOP_TOKEN_URL))
    with patch("whoop.auth.oauth.httpx.post", return_value=bad), \
         patch("webbrowser.open", side_effect=AssertionError("browser")), \
         patch("builtins.input", side_effect=AssertionError("input")):
        result = runner.invoke(app, ["raw", "profile"])
    assert result.exit_code == 3
    assert "REAUTH_REQUIRED" in result.output
    assert TokenStore(str(env)).load().refresh_token == "r"


def test_data_command_with_network_failure_exits_4(env):
    TokenStore(str(env)).save(TokenData("a", "r", time.time() - 100))
    with patch("whoop.auth.oauth.httpx.post", side_effect=httpx.ConnectTimeout("t")):
        result = runner.invoke(app, ["raw", "profile"])
    assert result.exit_code == 4
    assert "TRANSIENT_ERROR" in result.output


def test_summary_with_invalid_date_exits_1_without_traceback(env):
    TokenStore(str(env)).save(TokenData("a", "r", time.time() + 3600))
    result = runner.invoke(app, ["summary", "--date", "2026-13-01"])
    assert result.exit_code == 1
    assert "YYYY-MM-DD" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


# ---------------------------------------------------------------------------
# auth check / status details / logout
# ---------------------------------------------------------------------------

def _api_response(status, body=None):
    return httpx.Response(status, json=body if body is not None else {},
                          request=httpx.Request("GET", "https://api.example/v2/user/profile/basic"))


@pytest.fixture
def valid_tokens(env):
    TokenStore(str(env)).save(TokenData("access", "refresh", time.time() + 3600, scope="offline read:profile"))
    return env


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr("whoop.api.client._TRANSIENT_BACKOFF_SECONDS", (0, 0))


def test_check_ok_exits_0(valid_tokens):
    with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=_api_response(200, {"user_id": 1}))):
        result = runner.invoke(app, ["auth", "check"])
    assert result.exit_code == 0
    assert "Check OK" in result.output


def test_check_revoked_exits_3_and_keeps_tokens(valid_tokens):
    bad_refresh = httpx.Response(400, json={"error": "invalid_request"},
                                 request=httpx.Request("POST", WHOOP_TOKEN_URL))
    with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=_api_response(401))), \
         patch("whoop.auth.oauth.httpx.post", return_value=bad_refresh):
        result = runner.invoke(app, ["auth", "check"])
    assert result.exit_code == 3
    assert "REAUTH_REQUIRED" in result.output
    assert TokenStore(str(valid_tokens)).load().refresh_token == "refresh"


def test_check_network_failure_exits_4(valid_tokens, no_backoff):
    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=httpx.ConnectTimeout("t"))):
        result = runner.invoke(app, ["auth", "check"])
    assert result.exit_code == 4
    assert "TRANSIENT_ERROR" in result.output


def test_status_prints_missing_scopes(valid_tokens):
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "Missing scopes" in result.output
    assert "read:recovery" in result.output


def test_logout_removes_tokens(valid_tokens):
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0
    assert not valid_tokens.exists()


def test_unknown_auth_action_exits_1(env):
    result = runner.invoke(app, ["auth", "nope"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Data commands: render without crashing (service mocked, v2 records)
# ---------------------------------------------------------------------------

@pytest.fixture
def svc(monkeypatch):
    service = MagicMock()
    service.aclose = AsyncMock()
    service.get_daily_summary = AsyncMock(return_value=DailySummary(
        date="2026-03-17", recovery_score=74, sleep_score=82, hrv_rmssd=55.2,
        resting_hr=58, strain=10.5, recommendation="Good recovery.", emoji="🟢"))
    service.get_recovery = AsyncMock(return_value=make_recovery_v2(score=74))
    service.get_sleep = AsyncMock(return_value=make_sleep_v2(score=82))
    service.get_body_measurement = AsyncMock(return_value=make_body_measurement())
    service.get_trends = AsyncMock(return_value=TrendReport(
        days=7, from_date="2026-03-10", to_date="2026-03-17",
        metrics=[MetricTrend("recovery_score", 70.0, 65.0, 75.0, "↑", 15.4)]))
    service.get_export = AsyncMock(return_value={"days": 7, "body": {}, "daily": []})
    service.get_profile = AsyncMock(return_value={"user_id": 1})
    monkeypatch.setattr("cli.main._get_service", lambda: service)
    return service


@pytest.mark.parametrize("args", [
    ["summary"], ["summary", "--raw"],
    ["recovery"], ["recovery", "--raw"],
    ["sleep"], ["sleep", "--raw"],
    ["body"], ["body", "--raw"],
    ["trends"], ["trends", "--raw"],
    ["export", "--days", "7"],
    ["raw", "profile"],
])
def test_data_commands_render(svc, args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert result.exception is None
    svc.aclose.assert_awaited()


def test_raw_flags_emit_json(svc):
    result = runner.invoke(app, ["recovery", "--raw"])
    assert json.loads(result.output)["score"] == 74


@pytest.mark.parametrize("command", ["recovery", "sleep"])
def test_empty_period_is_not_an_error(svc, command):
    getattr(svc, f"get_{command}").return_value = None
    result = runner.invoke(app, [command])
    assert result.exit_code == 0
    assert "No " in result.output


def test_export_to_file(svc, tmp_path):
    out = tmp_path / "export.json"
    result = runner.invoke(app, ["export", "--days", "7", "--output", str(out)])
    assert result.exit_code == 0
    assert json.loads(out.read_text())["days"] == 7


def test_export_invalid_days_exits_1(svc):
    svc.get_export.side_effect = ValueError("days must be between 1 and 90")
    result = runner.invoke(app, ["export", "--days", "500"])
    assert result.exit_code == 1


def test_raw_unknown_endpoint_exits_1(svc):
    result = runner.invoke(app, ["raw", "nope"])
    assert result.exit_code == 1

