import json
from mcp.types import Tool
from whoop.services import WhoopService

TOOL = Tool(
    name="get_daily_summary",
    description=(
        "Get an aggregated daily health summary including recovery score, sleep score, "
        "HRV, resting HR, strain, and a human-readable training recommendation. "
        "This is the primary tool for Coach to use for morning briefings."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Date in YYYY-MM-DD format. Defaults to today.",
            },
        },
    },
)


async def handle(arguments: dict, service: WhoopService) -> str:
    summary = await service.get_daily_summary(date=arguments.get("date"))
    return json.dumps(summary.to_dict(), indent=2)
