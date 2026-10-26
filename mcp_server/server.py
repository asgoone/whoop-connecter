"""
WHOOP MCP Server entry point.
Transport: stdio (for OpenClaw integration).
"""

import asyncio
import json
import logging
import os
import sys

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from whoop.errors import WhoopError
from whoop.services import WhoopService, _build_service_from_env

from .tools import auth_status, body, profile, recovery, sleep, workouts, cycles, summary, trends

logger = logging.getLogger(__name__)

_TOOL_MODULES = [
    auth_status,
    body,
    profile,
    recovery,
    sleep,
    workouts,
    cycles,
    summary,
    trends,
]


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,  # MCP uses stdout for the protocol; logs must go to stderr
    )


def _error_result(error: dict) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"error": error}, indent=2))],
        isError=True,
    )


def create_server(service: WhoopService) -> Server:
    server = Server("whoop-mcp")

    tool_map: dict[str, object] = {mod.TOOL.name: mod for mod in _TOOL_MODULES}
    tool_list: list[Tool] = [mod.TOOL for mod in _TOOL_MODULES]

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tool_list

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> CallToolResult:
        mod = tool_map.get(name)
        if mod is None:
            return _error_result({"code": "UNKNOWN_TOOL", "message": f"Unknown tool: {name}"})
        try:
            result = await mod.handle(arguments or {}, service)
        except WhoopError as exc:
            logger.warning("Tool %s failed: %s", name, exc.code.value)
            return _error_result(exc.to_dict())
        except ValueError as exc:
            return _error_result({"code": "INVALID_ARGUMENTS", "message": str(exc)})
        except Exception as exc:
            # Exception text may contain request details; keep it in stderr logs only
            logger.exception("Unexpected error in tool %s", name)
            return _error_result({"code": "INTERNAL_ERROR", "message": type(exc).__name__})
        return CallToolResult(content=[TextContent(type="text", text=result)], isError=False)

    return server


async def run() -> None:
    load_dotenv()
    _setup_logging()

    try:
        service = _build_service_from_env()
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
        sys.exit(1)
    except (EnvironmentError, ValueError) as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)

    server = create_server(service)

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await service.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
