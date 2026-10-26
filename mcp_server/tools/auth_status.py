import json
from mcp.types import Tool
from whoop.services import WhoopService

TOOL = Tool(
    name="get_auth_status",
    description=(
        "Check WHOOP authorization. By default reads the stored token state only "
        "(state: valid | refresh_needed | auth_required | reauth_required | store_error). "
        "Set verify=true to also make a real WHOOP API call that proves access works; "
        "check.code REAUTH_REQUIRED means the owner must log in again."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "verify": {
                "type": "boolean",
                "description": "Make a live WHOOP API call to confirm access (may refresh the token).",
            },
        },
    },
)


async def handle(arguments: dict, service: WhoopService) -> str:
    if arguments.get("verify"):
        result = await service.auth_check()
    else:
        result = service.auth_status()
    return json.dumps(result, indent=2)
