"""
CLI exit codes and passive auth status. No network: tokens are absent,
damaged, or the token endpoint is mocked.
"""

import time
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from cli.main import app
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
