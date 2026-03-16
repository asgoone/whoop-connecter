import json
from mcp.types import Tool
from whoop.services import WhoopService

TOOL = Tool(
    name="get_auth_status",
    description="Check WHOOP OAuth token status: authenticated, expiry time, and whether it has expired.",
    inputSchema={"type": "object", "properties": {}},
)


async def handle(arguments: dict, service: WhoopService) -> str:
    result = service.auth_status()
    return json.dumps(result, indent=2)
