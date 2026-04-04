"""Resource: list_organizations"""

import json

from app import server, _client


@server.resource("flow://orgs")
async def list_organizations() -> str:
    """List all organizations the current user belongs to.

    Returns org id, name, role, and member count for each organization.
    Use this to find org IDs before creating nodes.
    """
    result = await _client().list_organizations()
    return json.dumps(result, indent=2, default=str)
