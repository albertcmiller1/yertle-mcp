"""Response transformations for LLM-friendly output.

Transforms raw Flow API responses into enriched formats where UUIDs are
resolved to human-readable names and a topology summary is generated.
"""


def transform_complete_state(raw: dict) -> dict:
    """Transform raw complete-state response into LLM-friendly format.

    Transformations:
    1. Build {uuid -> title} lookup from child_nodes
    2. Merge visual_properties into child_nodes as "components"
       (position + size inlined per component)
    3. Resolve connection UUIDs to human-readable names
    4. Generate a topology summary string
    5. Return { system, components, connections, topology_summary }
    """
    node = raw.get("node", {})

    # 1. Build ID -> title lookup
    id_to_title: dict[str, str] = {}
    for child in raw.get("child_nodes", []):
        id_to_title[child["id"]] = child.get("title", child["id"])

    # 2. Merge visual_properties into child_nodes as "components"
    vp_by_child: dict[str, dict] = {}
    for vp in raw.get("visual_properties", []):
        vp_by_child[vp["child_node_id"]] = vp

    components = []
    for child in raw.get("child_nodes", []):
        vp = vp_by_child.get(child["id"], {})
        components.append({
            "id": child["id"],
            "title": child.get("title", ""),
            "description": child.get("description", ""),
            "tags": child.get("tags", {}),
            "position": {
                "x": vp.get("position_x", 0),
                "y": vp.get("position_y", 0),
            },
            "size": {
                "width": vp.get("width", 200),
                "height": vp.get("height", 100),
            },
        })

    # 3. Resolve connection UUIDs to names
    connections = []
    for conn in raw.get("connections", []):
        from_id = conn.get("from_child_id", "")
        to_id = conn.get("to_child_id", "")
        connections.append({
            "id": conn.get("id", ""),
            "from": id_to_title.get(from_id, from_id),
            "from_id": from_id,
            "to": id_to_title.get(to_id, to_id),
            "to_id": to_id,
            "label": conn.get("label", ""),
            "from_edge": conn.get("from_edge", "right"),
            "to_edge": conn.get("to_edge", "left"),
        })

    # 4. Topology summary
    topology = _build_topology_summary(connections)

    return {
        "system": {
            "title": node.get("title", ""),
            "description": node.get("description", ""),
            "id": node.get("id", ""),
            "tags": raw.get("tags", {}),
        },
        "components": components,
        "connections": connections,
        "topology_summary": topology,
    }


def transform_canvas_state(raw: dict) -> dict:
    """Pass through canvas state (coordinates are already 0-centered)."""
    return raw


def _build_topology_summary(connections: list[dict]) -> str:
    """Build a one-line text summary of the connection graph.

    Example: "Load Balancer -> [API Gateway]. API Gateway -> [Tweet Service, User Service]."
    """
    if not connections:
        return "No connections."

    # Group targets by source
    from_to: dict[str, list[str]] = {}
    for conn in connections:
        src = conn.get("from", "?")
        tgt = conn.get("to", "?")
        from_to.setdefault(src, []).append(tgt)

    parts = []
    for src, targets in from_to.items():
        if len(targets) == 1:
            parts.append(f"{src} \u2192 {targets[0]}")
        else:
            parts.append(f"{src} \u2192 [{', '.join(targets)}]")

    return ". ".join(parts) + "."
