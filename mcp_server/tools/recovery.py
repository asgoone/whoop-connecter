import json
from mcp.types import Tool
from whoop.services import WhoopService
from whoop.schema.mappers import map_recovery

TOOL = Tool(
    name="get_recovery",
    description=(
        "Retrieve WHOOP recovery data for a given date range. "
        "Returns recovery score (0-100), HRV (RMSSD), resting heart rate, and SpO2."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "start": {
                "type": "string",
                "description": "Start datetime in ISO format (e.g. 2026-03-16T00:00:00.000Z). Defaults to today.",
            },
            "end": {
                "type": "string",
                "description": "End datetime in ISO format. Defaults to end of today.",
            },
        },
    },
)


async def handle(arguments: dict, service: WhoopService) -> str:
    raw = await service.get_recovery(
        start=arguments.get("start"),
        end=arguments.get("end"),
    )
    if raw is None:
        return json.dumps({"error": "No recovery data found for the requested period."})

    mapped = map_recovery(raw)
    return json.dumps({
        "score": mapped.score,
        "hrv_rmssd": mapped.hrv_rmssd,
        "resting_hr": mapped.resting_hr,
        "spo2": mapped.spo2,
        "skin_temp_deviation": mapped.skin_temp_deviation,
    }, indent=2)
