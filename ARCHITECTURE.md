# Architecture

This document describes how `yertle-mcp` is structured internally and why the code is split the way it is.

## Design Intent

The repository is built around a narrow goal: provide an MCP-friendly layer over the Flow backend without putting backend-specific complexity directly into tool handlers.

That results in four layers:

1. bootstrap and lifecycle
2. high-level client orchestration
3. endpoint-specific HTTP wrappers and state transforms
4. MCP-facing tools and resources

The code is small, but the separation is deliberate.

## Runtime Flow

### Startup

`server.py` is the entrypoint. Its only real jobs are:

- import the shared `server` object from `app.py`
- import `tools` and `resources` so decorators execute
- call `server.run(transport="stdio")`

`app.py` creates the `FastMCP` instance and its lifespan handler. On startup the lifespan function:

1. constructs `Config`
2. constructs `FlowClient`
3. calls `FlowClient.connect()`
4. stores the config and client on the server instance

On shutdown it closes the shared HTTP client.

This means all tool and resource handlers depend on a single authenticated `FlowClient`, rather than creating their own connections.

### Request Handling

When a tool or resource is invoked:

1. the decorated handler in `tools/` or `resources/` receives MCP input
2. it fetches the shared client via `_client()` or config via `_config()`
3. it calls a high-level method on `FlowClient`
4. `FlowClient` delegates the raw HTTP request to `client/api.py`
5. the HTTP response is normalized by `_handle_response()`
6. the tool or resource returns JSON text to the MCP caller

Resources optionally apply response transforms before returning. Tools are mostly pass-through adapters with argument shaping and defaults.

## Module Responsibilities

### `app.py`

This is the composition root.

Key responsibilities:

- instantiate `FastMCP`
- define lifecycle setup/teardown
- provide the long instruction string that guides LLM layout behavior
- expose `_client()` and `_config()` helper accessors

The instruction string is operationally important. It is not just documentation; it encodes layout constraints, viewport guidance, resource discovery advice, and editing cautions for the model using the MCP server.

### `config.py`

`Config` is intentionally strict:

- auth credentials are required at construction time
- `FLOW_API_URL` has a sensible local default
- `FLOW_DEFAULT_ORG_ID` is optional

The important convenience method is `resolve_org_id()`. It lets MCP handlers accept an optional `org_id` while still producing a clear failure when neither an explicit org ID nor a default org is available.

### `flow_client.py`

`FlowClient` is the center of the implementation.

It owns:

- the `httpx.AsyncClient`
- access and refresh token state
- sign-in and refresh behavior
- retry-on-401 logic
- mapping of repo-level operations to low-level API wrappers
- normalization of error messages

This keeps auth and transport concerns out of tools/resources.

It also owns the write path for pushes:

- fetch current complete state
- compute `expected_head_commit`
- merge and validate the incoming partial state
- send the final snapshot to the backend

That logic belongs here because it is application behavior, not endpoint wiring.

### `client/api.py`

This module is intentionally dumb. Each function corresponds to a single HTTP request and returns an `httpx.Response`.

It does not:

- interpret errors
- refresh tokens
- merge state
- transform output

That makes it easy to extend and easy to audit. If an API endpoint changes, this is the first file to update.

### `client/push.py`

This is the most behavior-heavy module in the repo.

The backend push API expects a complete state snapshot. An LLM, however, is much more likely to produce partial intent such as:

- add one child node
- add one connection
- update a title

`prepare_push_state()` bridges that mismatch.

Its pipeline is:

1. merge current state with incoming partial state
2. validate required fields
3. auto-center visual properties when the incoming update includes them
4. default transparency to `0.2`
5. default connection edges to `right` and `left`

#### Merge Strategy

The merge behavior is the main safety property in the repo.

Base state is reconstructed from the backend's complete-state response:

- `node.title` and `node.description` come from `raw["node"]`
- `tags` and `directories` are lifted out of `raw["node"]`
- `visual_properties` come from the current snapshot
- `connections` come from `child_node_connections`

Incoming sections are then applied as follows:

- `visual_properties` are merged by `child_node_id`
- `connections` are merged by `id`
- everything else is replaced wholesale

This avoids accidental deletion when the caller only wants to add a node or connection.

#### Validation

The validator prevents malformed pushes from failing later in backend internals with poor error messages.

It currently checks:

- every visual property has `child_node_id`
- every connection has `id`, `from_child_id`, and `to_child_id`

This is lightweight but valuable because those are the fields most likely to be omitted by an LLM.

### `client/transform.py`

This module makes backend responses easier for an LLM to consume.

For complete node state, it:

- builds an ID-to-title lookup from child nodes
- inlines visual layout data into component objects
- resolves connection endpoints to human-readable names
- builds a one-line topology summary

That topology summary is especially useful for quick model comprehension and terminal rendering.

Canvas state is currently passed through unchanged.

### `tools/`

The tool layer is intentionally thin. Its job is to expose high-value actions while keeping business logic lower in the stack.

Notable behavior in `tools/node.py`:

- `create_node()` injects default tags and a default directory based on the configured email username
- `push_node_state()` documents strict conventions around UUIDs, new-node placement, and temporary connection IDs
- search and branch operations primarily shape inputs and forward them

The docs in these tool functions are part of the product surface. They are designed to reduce bad LLM calls before they happen.

### `resources/`

Resources serve read-heavy discovery workflows.

The split is sensible:

- `flow://orgs` for organization discovery
- `flow://orgs/{org_id}/nodes` for top-level node exploration
- `.../complete` for architecture understanding
- `.../canvas` for layout-oriented inspection

The `complete` resource is the most semantically enriched resource because it passes through `transform_complete_state()`.

## Why The Server Works Well For MCP

This repo is opinionated in a way that fits LLM-driven usage:

- the instruction block teaches layout and editing rules up front
- resources support discovery before mutation
- tools are narrow and descriptive
- push merging protects against the backend's full-snapshot semantics
- transformed responses trade raw fidelity for model usability where it matters

In short, the repo is not just an API proxy. It is a behavioral adapter between an LLM and Flow.

## Extension Points

The cleanest way to add new functionality is to preserve the current layering:

1. add an endpoint wrapper to `client/api.py`
2. expose a higher-level operation in `flow_client.py`
3. decide whether the capability is better modeled as a resource or tool
4. add transforms only if the backend response is awkward for model consumption

That keeps the MCP layer readable and avoids duplicating auth, retry, or error handling.

## Current Tradeoffs

A few design tradeoffs stand out from the code:

- The server stores shared runtime objects on the `FastMCP` instance using private-looking attributes (`_flow_config`, `_flow_client`). This is simple, but informal.
- `get_complete_state()` and `get_canvas_state()` in `FlowClient` are hardcoded to the `main` branch, even though push operations accept a `branch` argument.
- `transform_canvas_state()` is a pass-through despite its docstring implying enrichment.
- The repo has no visible test suite or dependency manifest in the current tree, so behavior is documented mostly through code and docstrings.

None of those are fatal, but they are the main architectural constraints visible in the current codebase.

## Suggested Mental Model

If you need to reason about the repo quickly, treat it like this:

- `server.py` and `app.py` boot the MCP runtime
- `tools/` and `resources/` define the external contract
- `flow_client.py` is the application service layer
- `client/api.py` is transport wiring
- `client/push.py` and `client/transform.py` are the adaptation layer that makes Flow practical for LLM use

That model maps closely to the actual implementation.
