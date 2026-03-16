import json
from mcp.types import Tool
from whoop.services import WhoopService
from whoop.schema.mappers import map_sleep

TOOL = Tool(
    name="get_sleep",
    description=(
        "Retrieve WHOOP sleep data. "
        "Returns sleep score (0-100), duration in hours, efficiency, and sleep stage breakdown."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "Start datetime ISO format."},
            "end": {"type": "string", "description": "End datetime ISO format."},
        },
    },
)


async def handle(arguments: dict, service: WhoopService) -> str:
    raw = await service.get_sleep(
        start=arguments.get("start"),
        end=arguments.get("end"),
    )
    if raw is None:
        return json.dumps({"error": "No sleep data found for the requested period."})

    mapped = map_sleep(raw)
    return json.dumps({
        "score": mapped.score,
        "duration_hours": mapped.duration_hours,
        "efficiency": mapped.efficiency,
        "stages": mapped.stages,
    }, indent=2)
