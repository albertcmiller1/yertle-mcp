"""Tools: create_node, push_node_state, delete_node"""

import json

from app import server, _client, _config


@server.tool()
async def create_node(
    title: str,
    org_id: str | None = None,
    description: str | None = None,
    tags: dict | None = None,
    directories: list[str] | None = None,
    public_id: str | None = None,
) -> str:
    """Create a new node in Flow.

    Each node is an independent entity. To place it on a canvas (as a child
    of another node), first create it here, then use push_node_state on the
    parent node to set its position and connections.

    Args:
        title: Node title (required).
        org_id: Organization UUID. Uses default org if omitted.
        description: Optional node description.
        tags: Optional key-value tag pairs (e.g. {"env": "prod"}).
        directories: Optional list of directory paths.
        public_id: Optional URL-friendly ID (auto-generated if omitted).

    Returns:
        JSON with the new node's id, title, description, org_id, public_id.
    """
    resolved_org = _config().resolve_org_id(org_id)

    # Default tags — LLM-provided values take precedence
    default_tags = {"ARN": "", "GitHub": "", "Team": "", "Runbook": ""}
    if tags:
        default_tags.update(tags)
    merged_tags = default_tags

    # Default directory — /users/<username from email>
    username = _config().user_email.split("@")[0]
    default_dirs = [f"/users/{username}"]
    if directories:
        # Merge: add defaults that aren't already present
        merged_dirs = list(directories)
        for d in default_dirs:
            if d not in merged_dirs:
                merged_dirs.append(d)
    else:
        merged_dirs = default_dirs

    result = await _client().create_node(
        org_id=resolved_org,
        title=title,
        description=description,
        tags=merged_tags,
        directories=merged_dirs,
        public_id=public_id,
    )
    return json.dumps(result, indent=2, default=str)


@server.tool()
async def push_node_state(
    node_id: str,
    message: str,
    state: dict,
    org_id: str | None = None,
    branch: str = "main",
) -> str:
    """Push state to a node — attach children, set positions, create connections.

    This is the primary tool for assembling diagrams. The MCP server
    automatically merges your changes with the current state:
    - node, tags, directories: replaced entirely if provided
    - visual_properties: merged by child_node_id (existing items preserved,
      new items added, matching items updated)
    - connections: merged by id (existing preserved, new added)
    You only need to include items you want to add or change.

    IMPORTANT — read carefully:
    - Do NOT re-send visual_properties for nodes that already exist on the canvas
      unless the user explicitly asks to rearrange or redesign the layout.
      Only include visual_properties for NEW child nodes you are adding.
      Re-sending existing nodes will overwrite their current positions.
    - All IDs (node_id, child_node_id, from_child_id, to_child_id) MUST be
      UUIDs returned by create_node, NOT public_ids or titles.
    - All child nodes referenced must already exist (create them first).
    - For new connections, use "temp-<label>" as the id (e.g. "temp-api-to-db").
      The backend generates real UUIDs and returns the mapping.
    - Coordinates are 0-centered: (0, 0) = canvas center. Lay out nodes left-to-right:
      increase x to go right, y to go down. Node size: 200x100.
      Horizontal spacing: 250px. Vertical spacing: 130px.
      Aim to fit the diagram within 1200x800px (laptop viewport), but it's OK to exceed if needed.

    Args:
        node_id: UUID of the parent node (from create_node response "id" field).
        message: Commit message describing what changed.
        state: State dict with any of these sections:
            - node: { title, description }
            - tags: { key: value, ... }
            - directories: ["/path/one", ...]
            - visual_properties: [{ child_node_id, position_x, position_y, width, height }]
              REQUIRED per item: child_node_id (UUID)
            - connections: [{ id, from_child_id, to_child_id, label, from_edge, to_edge }]
              REQUIRED per item: id (use "temp-<label>"), from_child_id (UUID), to_child_id (UUID)
              Edges: from_edge (default "right"), to_edge (default "left"). Valid: left, right, top, bottom.
              Optional: label
        org_id: Organization UUID. Uses default org if omitted.
        branch: Branch name (default: "main").

    Returns:
        JSON with commit_id, objects_created, and connection_id_mappings
        (maps "temp-<label>" to real UUIDs).

    Example state:
        {
            "visual_properties": [
                {"child_node_id": "<uuid-A>", "position_x": 0, "position_y": 0, "width": 200, "height": 100},
                {"child_node_id": "<uuid-B>", "position_x": 300, "position_y": 0, "width": 200, "height": 100}
            ],
            "connections": [
                {"id": "temp-a-to-b", "from_child_id": "<uuid-A>", "to_child_id": "<uuid-B>", "label": "calls"}
            ]
        }
    """
    resolved_org = _config().resolve_org_id(org_id)
    result = await _client().push_state(
        org_id=resolved_org,
        node_id=node_id,
        message=message,
        state=state,
        branch=branch,
    )
    return json.dumps(result, indent=2, default=str)


@server.tool()
async def delete_node(
    node_id: str,
    org_id: str | None = None,
) -> str:
    """Permanently delete a node and all its data (branches, commits, objects).

    This cannot be undone. The node will be removed from any parent canvases.

    Args:
        node_id: Node UUID or public_id.
        org_id: Organization UUID. Uses default org if omitted.

    Returns:
        Confirmation message.
    """
    resolved_org = _config().resolve_org_id(org_id)
    result = await _client().delete_node(resolved_org, node_id)
    return json.dumps(result, indent=2, default=str)


@server.tool()
async def search_nodes(
    org_id: str | None = None,
    title_contains: str | None = None,
    tags: dict | None = None,
    tag_match_mode: str | None = None,
    directories: list[str] | None = None,
    recursive_directories: bool = False,
    limit: int = 25,
    offset: int = 0,
) -> str:
    """Search for nodes in an organization with filters.

    Use this when the flow://orgs/{org_id}/nodes resource returns too many
    nodes, or when you need to find nodes by title, tags, or directory.

    Args:
        org_id: Organization UUID. Uses default org if omitted.
        title_contains: Case-insensitive substring match on node title.
        tags: Filter by tag key-value pairs (e.g. {"env": "prod"}).
        tag_match_mode: "all" (AND, default) or "any" (OR) for multiple tags.
        directories: Filter by directory paths (e.g. ["/infrastructure"]).
        recursive_directories: If true, include nodes in subdirectories.
        limit: Max results (default 25).
        offset: Skip first N results for pagination.

    Returns:
        JSON with matching nodes (id, title, description, tags, counts)
        and pagination info (total, limit, offset).
    """
    resolved_org = _config().resolve_org_id(org_id)

    filters: dict = {"limit": limit, "offset": offset}
    if title_contains is not None:
        filters["title_contains"] = title_contains
    if tags is not None:
        filters["tags"] = tags
    if tag_match_mode is not None:
        filters["tag_match_mode"] = tag_match_mode
    if directories is not None:
        filters["directories"] = directories
    if recursive_directories:
        filters["recursive_directories"] = recursive_directories

    result = await _client().search_nodes(resolved_org, filters)
    return json.dumps(result, indent=2, default=str)


@server.tool()
async def create_branch(
    node_id: str,
    name: str,
    base_branch: str = "main",
    org_id: str | None = None,
) -> str:
    """Create a new branch on a node, forking from an existing branch.

    Use this before pushing changes to a non-main branch. The new branch
    starts as a copy of base_branch's current state.

    Args:
        node_id: UUID of the node to branch.
        name: Branch name (alphanumeric, hyphens, underscores, 1-250 chars).
        base_branch: Branch to fork from (default: "main").
        org_id: Organization UUID. Uses default org if omitted.

    Returns:
        JSON with the new branch's name, head_commit, base_branch, and timestamps.
    """
    resolved_org = _config().resolve_org_id(org_id)
    result = await _client().create_branch(resolved_org, node_id, name, base_branch)
    return json.dumps(result, indent=2, default=str)


@server.tool()
async def list_branches(
    node_id: str,
    org_id: str | None = None,
) -> str:
    """List all branches on a node.

    Use this to discover existing branches before pushing to a non-main
    branch, or to check branch state (head commit, last activity).

    Args:
        node_id: UUID of the node.
        org_id: Organization UUID. Uses default org if omitted.

    Returns:
        JSON with branches (name, head_commit, base_branch, timestamps)
        and total count.
    """
    resolved_org = _config().resolve_org_id(org_id)
    result = await _client().list_branches(resolved_org, node_id)
    return json.dumps(result, indent=2, default=str)
