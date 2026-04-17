# yertle-mcp

`yertle-mcp` is a small MCP server that exposes Yertle's APIs to an LLM. It runs over stdio, authenticates against the Flow REST API, and registers MCP tools and resources for:

- creating organizations and nodes
- listing and searching nodes
- reading node state from Flow
- pushing diagram state changes back to Flow
- creating and listing branches

The codebase is intentionally thin. Most files do one job:

- `server.py` starts the stdio MCP server
- `app.py` builds the shared `FastMCP` instance and lifecycle
- `flow_client.py` manages auth, retries, and high-level API operations
- `client/api.py` contains one-function-per-endpoint HTTP wrappers
- `client/push.py` merges and validates partial state before push
- `client/transform.py` reshapes backend responses into LLM-friendly output
- `tools/` and `resources/` register the MCP surface area

## How It Works

At startup, `server.py` imports `app.py`, then imports `tools` and `resources`. Those imports matter because registration is decorator-driven.

During server lifespan:

1. `Config` reads environment variables.
2. `FlowClient` creates an `httpx.AsyncClient`.
3. The client signs in with email/password and stores JWTs.
4. MCP tools and resources use the shared client through helper accessors in `app.py`.

For write operations, especially `push_node_state`, the server does not send the LLM's partial state directly to the backend. Instead it:

1. fetches the current complete node state
2. merges the incoming partial update onto that snapshot
3. validates required fields
4. auto-centers new visual properties when present
5. applies defaults such as node transparency and connection edges
6. pushes the merged full snapshot to Flow

That merge step is the main safety feature in this repository.

## Environment

Required:

- `FLOW_USER_EMAIL`
- `FLOW_USER_PASSWORD`

Optional:

- `FLOW_API_URL` defaults to `http://localhost:8000`
- `FLOW_DEFAULT_ORG_ID` lets tools omit `org_id`

Example:

```bash
export FLOW_API_URL=http://localhost:8000
export FLOW_USER_EMAIL=you@example.com
export FLOW_USER_PASSWORD=secret
export FLOW_DEFAULT_ORG_ID=<org-uuid>
```

## Running

Run the MCP server with:

```bash
python3 server.py
```

The server is configured for stdio transport, which matches how MCP hosts usually launch it.

## MCP Surface

Resources:

- `flow://orgs`
- `flow://orgs/{org_id}/nodes`
- `flow://orgs/{org_id}/nodes/{node_id}/complete`
- `flow://orgs/{org_id}/nodes/{node_id}/canvas`

Tools:

- `create_organization`
- `create_node`
- `push_node_state`
- `delete_node`
- `search_nodes`
- `create_branch`
- `list_branches`

## Push Semantics

`push_node_state` is designed around Flow's full-snapshot push API, but the MCP interface accepts partial updates.

The server preserves existing state unless the caller explicitly replaces it:

- `node`, `tags`, and `directories` are replaced if included
- `visual_properties` are merged by `child_node_id`
- `connections` are merged by `id`

Two important rules are enforced by convention and validation:

- new or updated visual properties must include `child_node_id`
- connections must include `id`, `from_child_id`, and `to_child_id`

For new connections, the intended temporary format is `temp-<label>`, and the backend returns the real IDs.

## Layout Assumptions

The instruction block in `app.py` encodes layout policy for the LLM:

- diagrams should flow left to right
- `position_x` increases to the right
- `position_y` increases downward
- node size should usually be `200x100`
- horizontal spacing should usually be `250`
- vertical spacing should usually be `130`
- diagrams should stay compact when possible

The server also warns the LLM not to resend visual properties for existing nodes unless the user explicitly wants a rearrangement.

## File Map

```text
.
├── app.py
├── config.py
├── flow_client.py
├── server.py
├── client/
│   ├── api.py
│   ├── push.py
│   └── transform.py
├── resources/
│   ├── nodes.py
│   └── orgs.py
└── tools/
    ├── node.py
    └── org.py
```

## Extending The Server

To add a new capability:

1. add a low-level REST wrapper in `client/api.py`
2. expose it through `FlowClient` in `flow_client.py`
3. register a new MCP tool or resource in `tools/` or `resources/`
4. import that module through the package `__init__.py` so decorator registration happens

If the endpoint returns backend-native state that is hard for an LLM to use, add a transformation in `client/transform.py` rather than inflating the tool/resource layer.

## Internal Design Notes

- The repo uses a shared global `server` instance from `app.py`.
- `server._flow_config` and `server._flow_client` are attached during lifespan startup.
- Errors are normalized in `FlowClient._handle_response()` into readable runtime exceptions.
- Auth retry is centralized in `FlowClient._with_retry()`, so individual API wrappers stay simple.

For a deeper walkthrough of the request flow and module boundaries, see [`ARCHITECTURE.md`](./docs/ARCHITECTURE.md).
