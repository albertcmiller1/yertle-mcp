"""Tools: list_organizations, create_organization"""

import json

from app import server, _client


@server.tool()
async def list_organizations() -> str:
    """List all organizations the current user belongs to.

    Use this to discover available org IDs before calling other tools
    that require an org_id parameter.

    Returns:
        JSON with list of orgs (id, name, role, timestamps).
    """
    result = await _client().list_organizations()
    return json.dumps(result, indent=2, default=str)


@server.tool()
async def create_organization(name: str) -> str:
    """Create a new organization in Yertle.

    Organizations are the top-level container for nodes. You need an org
    before creating any nodes. Use the flow://orgs resource to see existing
    organizations before creating a new one.

    Args:
        name: Organization name (1-255 characters).

    Returns:
        JSON with the new org's id, name, role, and timestamps.
    """
    result = await _client().create_organization(name)
    return json.dumps(result, indent=2, default=str)
