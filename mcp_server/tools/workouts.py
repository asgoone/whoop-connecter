import json
from mcp.types import Tool
from whoop.services import WhoopService
from whoop.schema.mappers import map_workout

TOOL = Tool(
    name="get_workouts",
    description="Retrieve WHOOP workout data (sport, strain, duration, HR, calories).",
    inputSchema={
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "Start datetime ISO format."},
            "end": {"type": "string", "description": "End datetime ISO format."},
        },
    },
)


async def handle(arguments: dict, service: WhoopService) -> str:
    raws = await service.get_workouts(
        start=arguments.get("start"),
        end=arguments.get("end"),
    )
    workouts = [map_workout(r) for r in raws]
    return json.dumps(
        [
            {
                "sport": w.sport,
                "strain": w.strain,
                "duration_minutes": w.duration_minutes,
                "avg_hr": w.avg_hr,
                "max_hr": w.max_hr,
                "calories": w.calories,
                "started_at": w.started_at,
            }
            for w in workouts
        ],
        indent=2,
    )
