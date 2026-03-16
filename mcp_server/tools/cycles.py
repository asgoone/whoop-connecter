import json
from mcp.types import Tool
from whoop.services import WhoopService
from whoop.schema.mappers import map_cycle

TOOL = Tool(
    name="get_cycles",
    description="Retrieve WHOOP physiological cycles (strain, calories) for a date range.",
    inputSchema={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "Start datetime ISO format."},
            "end": {"type": "string", "description": "End datetime ISO format."},
        },
    },
)


async def handle(arguments: dict, service: WhoopService) -> str:
    raws = await service.get_cycles(
        start=arguments.get("start"),
        end=arguments.get("end"),
    )
    cycles = [map_cycle(r) for r in raws]
    return json.dumps(
        [
            {"strain": c.strain, "calories": c.calories}
            for c in cycles
        ],
        indent=2,
    )
