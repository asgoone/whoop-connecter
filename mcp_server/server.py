"""
WHOOP MCP Server entry point.
Transport: stdio (for OpenClaw integration).
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from whoop.api.client import WhoopAPIError
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


def create_server(service: WhoopService) -> Server:
    server = Server("whoop-mcp")

    tool_map: dict[str, object] = {mod.TOOL.name: mod for mod in _TOOL_MODULES}
    tool_list: list[Tool] = [mod.TOOL for mod in _TOOL_MODULES]

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tool_list

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        mod = tool_map.get(name)
        if mod is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = await mod.handle(arguments or {}, service)
        except WhoopAPIError as exc:
            logger.warning("WHOOP API error in tool %s: %s", name, exc)
            return [TextContent(type="text", text=f"WHOOP API error ({exc.status_code}): {exc}")]
        except ValueError as exc:
            return [TextContent(type="text", text=f"Invalid arguments: {exc}")]
        except Exception as exc:
            logger.exception("Unexpected error in tool %s", name)
            return [TextContent(type="text", text=f"Internal error: {exc}")]
        return [TextContent(type="text", text=result)]

    return server


async def run() -> None:
    load_dotenv()
    _setup_logging()

    try:
        service = _build_service_from_env()
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
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
