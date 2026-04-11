"""Push state preparation: merge, validate, auto-center, and apply defaults.

This module handles all the logic between receiving an LLM's partial state
and producing the final state dict ready for the Flow push API.
"""

import logging


logger = logging.getLogger("yertle")


def prepare_push_state(current_raw: dict, incoming_state: dict) -> dict:
    """Prepare a push-ready state dict from current state and LLM input.

    Steps:
    1. Merge incoming partial state onto current complete state
    2. Validate required fields
    3. Auto-center visual properties (if LLM provided new ones)
    4. Apply defaults (transparency, connection edges)

    Args:
        current_raw: Raw response from get_complete_state (API format)
        incoming_state: Partial state dict from the LLM's push_node_state call

    Returns:
        Merged, validated, and transformed state dict ready for the push API.
    """
    merged = _merge_state(current_raw, incoming_state)
    _validate_push_state(merged)

    vps = merged.get("visual_properties", [])

    # Auto-center: if the LLM provided new visual_properties, shift them
    # so the bounding box center lands at (0, 0).
    # Skip when visual_properties came from existing backend state (partial update).
    if vps and "visual_properties" in incoming_state:
        xs = [vp.get("position_x", 0) for vp in vps]
        ys = [vp.get("position_y", 0) for vp in vps]
        center_x = (min(xs) + max(xs)) / 2
        center_y = (min(ys) + max(ys)) / 2
        for vp in vps:
            vp["position_x"] = vp.get("position_x", 0) - center_x
            vp["position_y"] = vp.get("position_y", 0) - center_y

    # Default transparency to 0.2 for nodes created via MCP
    for vp in vps:
        vp.setdefault("transparency", 0.2)

    # Default connection edges to right/left (backend defaults to "center" which is invalid)
    for conn in merged.get("connections", []):
        conn.setdefault("from_edge", "right")
        conn.setdefault("to_edge", "left")

    return merged


def _merge_state(current_raw: dict, incoming: dict) -> dict:
    """Merge incoming partial state onto current complete state.

    The backend push API uses full-snapshot semantics — omitting a section
    deletes it. This method ensures safety by:
    - Starting with the current state as the base
    - Overlaying only the sections present in `incoming`
    - Sections not in `incoming` are preserved from current state

    Args:
        current_raw: Raw response from get_complete_state (API format)
        incoming: Partial state dict from the LLM's push_node_state call

    Returns:
        Merged state dict ready for the push API.
    """
    node = current_raw.get("node", {})

    # Build push-compatible base from complete response
    base_vps = [dict(vp) for vp in current_raw.get("visual_properties", [])]

    base = {
        "node": {
            "title": node.get("title", ""),
            "description": node.get("description", ""),
        },
        "tags": current_raw.get("tags", {}),
        "directories": current_raw.get("directories", []),
        "visual_properties": base_vps,
        "connections": current_raw.get("connections", []),
    }

    # Overlay incoming sections onto the base.
    # visual_properties and connections use item-level merge (additive)
    # so the LLM can add/update items without wiping existing ones.
    # All other sections use full replacement.
    for key in incoming:
        if key == "visual_properties":
            vp_map = {vp["child_node_id"]: vp for vp in base.get("visual_properties", [])}
            for vp in incoming["visual_properties"]:
                vp_map[vp["child_node_id"]] = vp
            base["visual_properties"] = list(vp_map.values())
        elif key == "connections":
            conn_map = {c["id"]: c for c in base.get("connections", [])}
            for c in incoming["connections"]:
                conn_map[c["id"]] = c
            base["connections"] = list(conn_map.values())
        else:
            base[key] = incoming[key]

    return base


def _validate_push_state(state: dict) -> None:
    """Validate state dict before sending to the push API.

    Catches missing required fields that would cause confusing KeyErrors
    in the backend's state_diff_service.
    """
    errors: list[str] = []

    for i, vp in enumerate(state.get("visual_properties", [])):
        if "child_node_id" not in vp:
            errors.append(
                f"visual_properties[{i}] missing 'child_node_id'. "
                "Each visual property must reference an existing child node UUID."
            )

    required_conn_fields = ("id", "from_child_id", "to_child_id")
    for i, conn in enumerate(state.get("connections", [])):
        missing = [f for f in required_conn_fields if f not in conn]
        if missing:
            errors.append(
                f"connections[{i}] missing {missing}. "
                "Each connection needs: id (use 'temp-<label>' for new), "
                "from_child_id, to_child_id."
            )

    if errors:
        raise ValueError(
            "Invalid push state:\n" + "\n".join(f"  - {e}" for e in errors)
        )
