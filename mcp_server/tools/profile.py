import json
from mcp.types import Tool
from whoop.services import WhoopService

TOOL = Tool(
    name="get_profile",
    description="Retrieve the WHOOP user profile (name, email, user_id).",
    inputSchema={"type": "object", "properties": {}},
)


async def handle(arguments: dict, service: WhoopService) -> str:
    result = await service.get_profile()
    return json.dumps(result, indent=2)
