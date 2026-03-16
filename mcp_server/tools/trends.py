import json
from mcp.types import Tool
from whoop.services import WhoopService

TOOL = Tool(
    name="get_trends",
    description=(
        "Get health metric trends over the last N days (7, 14, or 30). "
        "Shows direction (↑↓→) and percentage change for recovery, sleep, HRV, resting HR, and strain."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of days to analyze (1-90). Typical values: 7, 14, 30.",
                "default": 7,
            },
        },
    },
)


async def handle(arguments: dict, service: WhoopService) -> str:
    days = int(arguments.get("days", 7))
    try:
        report = await service.get_trends(days=days)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(report.to_dict(), indent=2)
