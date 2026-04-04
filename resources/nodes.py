"""Resources: node listing, complete state, canvas state"""

import json

from app import server, _client
from client.transform import transform_complete_state, transform_canvas_state


@server.resource("flow://orgs/{org_id}/nodes")
async def list_org_nodes(org_id: str) -> str:
    """List nodes in a specific organization (first 25).

    This is the PRIMARY resource for exploring nodes. Use this whenever the
    user asks about nodes in an org or what exists in an org.
    Requires an org_id — use flow://orgs first to find available org IDs.

    Returns up to 25 nodes with a total count. If the org has more nodes
    or you need to filter by title, tags, or directories, use the
    search_nodes tool instead.

    Returns each node's id, title, description, tags, and parent/child counts.
    """
    result = await _client().list_nodes(org_id, limit=25)
    return json.dumps(result, indent=2, default=str)


@server.resource("flow://orgs/{org_id}/nodes/{node_id}/complete")
async def get_node_complete(org_id: str, node_id: str) -> str:
    """Get the full state of a node on the main branch.

    URL: flow://orgs/{org_id}/nodes/{node_id}/complete
    Both org_id and node_id are required. If you don't know the org_id,
    use flow://orgs to list organizations first, or ask the user.

    Returns an LLM-enriched response with:
    - system: parent node info (title, description, tags)
    - components: child nodes with position and size inlined
    - connections: with human-readable names (not just UUIDs)
    - topology_summary: one-line text summary of the connection graph

    Use this to understand a system's architecture before making changes.
    When presenting results to the user, render an ASCII box-and-arrow diagram
    of the components and connections for a visual overview.
    """
    raw = await _client().get_complete_state(org_id, node_id)
    transformed = transform_complete_state(raw)
    return json.dumps(transformed, indent=2, default=str)


@server.resource("flow://orgs/{org_id}/nodes/{node_id}/canvas")
async def get_node_canvas(org_id: str, node_id: str) -> str:
    """Get the canvas state of a node on the main branch.

    URL: flow://orgs/{org_id}/nodes/{node_id}/canvas
    Both org_id and node_id are required. If you don't know the org_id,
    use flow://orgs to list organizations first, or ask the user.

    Returns a flattened, LLM-enriched view optimized for understanding
    the visual layout. Same enrichment as the complete state resource.
    """
    result = await _client().get_canvas_state(org_id, node_id)
    transformed = transform_canvas_state(result)
    return json.dumps(transformed, indent=2, default=str)
