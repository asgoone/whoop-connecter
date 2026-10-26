"""
End-to-end MCP over real stdio: initialize, list_tools, call_tool.
Runs the server as a subprocess with dummy credentials and no stored tokens,
so no request ever reaches WHOOP.
"""

import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CLIENT_SECRET = "dummy-client-secret-value"
ENC_KEY = "c" * 64


@pytest.fixture
def server_params(tmp_path):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        env={
            "WHOOP_CLIENT_ID": "dummy-client-id",
            "WHOOP_CLIENT_SECRET": CLIENT_SECRET,
            "WHOOP_TOKEN_ENCRYPTION_KEY": ENC_KEY,
            "WHOOP_TOKEN_PATH": str(tmp_path / "tokens.enc"),
            "LOG_LEVEL": "DEBUG",
        },
        cwd=str(Path(__file__).resolve().parents[2]),  # repo root: mcp_server importable
    )


async def test_initialize_list_and_call_without_tokens(server_params, tmp_path):
    stderr_path = tmp_path / "stderr.log"

    with stderr_path.open("w") as errlog:
        async with stdio_client(server_params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "whoop-mcp"

                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert "get_daily_summary" in names
                assert not any("login" in n or "logout" in n for n in names)

                status = await session.call_tool("get_auth_status", {})
                assert status.isError is False
                assert json.loads(status.content[0].text)["state"] == "auth_required"

                result = await session.call_tool("get_profile", {})
                assert result.isError is True
                error = json.loads(result.content[0].text)["error"]
                assert error["code"] == "AUTH_REQUIRED"

                unknown = await session.call_tool("nope", {})
                assert unknown.isError is True

    # No token file was created, no interactive flow was started
    assert not (tmp_path / "tokens.enc").exists()
    stderr = stderr_path.read_text()
    assert CLIENT_SECRET not in stderr
    assert ENC_KEY not in stderr
    assert "Opening browser" not in stderr
