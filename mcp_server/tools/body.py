import json
from mcp.types import Tool
from whoop.services import WhoopService
from whoop.schema.mappers import map_body_measurement

TOOL = Tool(
    name="get_body_measurement",
    description=(
        "Retrieve WHOOP body measurements: height (meters), weight (kg), "
        "and max heart rate (bpm). Useful for calculating BMI, calorie targets, "
        "and personalizing training recommendations."
    ),
    inputSchema={"type": "object", "properties": {}},
)


async def handle(arguments: dict, service: WhoopService) -> str:
    raw = await service.get_body_measurement()
    mapped = map_body_measurement(raw)
    return json.dumps(mapped.to_dict(), indent=2)
