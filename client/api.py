"""HTTP wrapper functions for the Flow REST API.

Each function makes a single HTTP request and returns the parsed response.
These are called by FlowClient, which provides the httpx client and auth headers.
"""

import httpx
from typing import Any


async def create_organization(
    client: httpx.AsyncClient, headers: dict, name: str
) -> httpx.Response:
    """POST /orgs — create a new organization."""
    return await client.post("/orgs", json={"name": name}, headers=headers)


async def create_node(
    client: httpx.AsyncClient,
    headers: dict,
    org_id: str,
    title: str,
    description: str | None = None,
    tags: dict | None = None,
    directories: list[str] | None = None,
    public_id: str | None = None,
) -> httpx.Response:
    """POST /orgs/{org_id}/nodes — create a new node."""
    body: dict[str, Any] = {"title": title}
    if description is not None:
        body["description"] = description
    if tags is not None:
        body["tags"] = tags
    if directories is not None:
        body["directories"] = directories
    if public_id is not None:
        body["public_id"] = public_id

    return await client.post(f"/orgs/{org_id}/nodes", json=body, headers=headers)


async def push_raw(
    client: httpx.AsyncClient,
    headers: dict,
    org_id: str,
    node_id: str,
    branch: str,
    message: str,
    state: dict,
    expected_head_commit: str = "",
) -> httpx.Response:
    """PUT /orgs/{org_id}/nodes/{node_id}/tree/{branch}/push — raw push."""
    return await client.put(
        f"/orgs/{org_id}/nodes/{node_id}/tree/{branch}/push",
        json={"message": message, "state": state, "expected_head_commit": expected_head_commit},
        headers=headers,
    )


async def delete_node(
    client: httpx.AsyncClient, headers: dict, org_id: str, node_id: str
) -> httpx.Response:
    """DELETE /orgs/{org_id}/nodes/{node_id} — permanently delete a node."""
    return await client.delete(f"/orgs/{org_id}/nodes/{node_id}", headers=headers)


async def list_organizations(
    client: httpx.AsyncClient, headers: dict
) -> httpx.Response:
    """GET /orgs — list all orgs the current user belongs to."""
    return await client.get("/orgs", headers=headers)


async def list_nodes(
    client: httpx.AsyncClient, headers: dict, org_id: str,
    limit: int = 25, offset: int = 0,
) -> httpx.Response:
    """GET /orgs/{org_id}/nodes — list nodes in an org (paginated)."""
    return await client.get(
        f"/orgs/{org_id}/nodes",
        params={"limit": limit, "offset": offset},
        headers=headers,
    )


async def search_nodes(
    client: httpx.AsyncClient, headers: dict, org_id: str, filters: dict,
) -> httpx.Response:
    """POST /orgs/{org_id}/nodes/search — search nodes with filters."""
    return await client.post(
        f"/orgs/{org_id}/nodes/search", json=filters, headers=headers,
    )


async def create_branch(
    client: httpx.AsyncClient, headers: dict,
    org_id: str, node_id: str, name: str, base_branch: str = "main",
) -> httpx.Response:
    """POST /orgs/{org_id}/nodes/{node_id}/branches — create a branch."""
    return await client.post(
        f"/orgs/{org_id}/nodes/{node_id}/branches",
        json={"name": name, "base_branch": base_branch},
        headers=headers,
    )


async def list_branches(
    client: httpx.AsyncClient, headers: dict, org_id: str, node_id: str,
) -> httpx.Response:
    """GET /orgs/{org_id}/nodes/{node_id}/branches — list branches."""
    return await client.get(
        f"/orgs/{org_id}/nodes/{node_id}/branches", headers=headers,
    )


async def get_complete_state(
    client: httpx.AsyncClient, headers: dict, org_id: str, node_id: str
) -> httpx.Response:
    """GET /orgs/{org_id}/nodes/{node_id}/tree/main/complete"""
    return await client.get(
        f"/orgs/{org_id}/nodes/{node_id}/tree/main/complete", headers=headers
    )


async def get_canvas_state(
    client: httpx.AsyncClient, headers: dict, org_id: str, node_id: str
) -> httpx.Response:
    """GET /orgs/{org_id}/nodes/{node_id}/tree/main/canvas"""
    return await client.get(
        f"/orgs/{org_id}/nodes/{node_id}/tree/main/canvas", headers=headers
    )
