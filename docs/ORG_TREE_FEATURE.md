# Feature: Org Tree Overview for AI Agents

## Problem

Yertle's value proposition is that every system in a company is modeled in one place — giving the CTO and the intern the same view. But AI agents using the MCP server today can't efficiently get that view.

The current workflow for an agent to understand an org:

```
1. Read flow://orgs                                    → list of orgs
2. Read flow://orgs/{id}/nodes                         → flat list of 25 nodes (no hierarchy)
3. Read flow://orgs/{id}/nodes/{root}/complete          → root's immediate children
4. Read flow://orgs/{id}/nodes/{child_a}/complete       → child A's children + connections
5. Read flow://orgs/{id}/nodes/{child_b}/complete       → child B's children + connections
6. ... repeat for every node with children
```

For an org with 3 levels of depth and 25 nodes, that's **10+ sequential tool calls** before the agent has a mental model. Each `complete` resource only shows one level of the hierarchy. The agent accumulates thousands of tokens of JSON across calls, much of it redundant (tags, positions, UUIDs).

This is the opposite of what we want. If Yertle is the source of truth for how a company's software works, an agent should be able to understand the full picture in **one call**.

## Solution

A new MCP resource — `flow://orgs/{org_id}/tree` — that returns a compact, recursive overview of every node in an org, their hierarchy, and how they connect at each level.

### Why text instead of JSON?

The output is **indented text**, not nested JSON. Reasons:

1. **Token efficiency.** A 130-node org rendered as nested JSON with descriptions, IDs, and connections is ~15-25k tokens. The same org as indented text with inline topology is ~3-5k tokens. That's a 5x difference — meaningful when context windows matter.

2. **Agents don't parse, they understand.** An LLM reading `├── Model Router → Inference Engine (completions) → Streaming Service (SSE)` understands the data flow instantly. It doesn't need to traverse a JSON tree programmatically.

3. **Drill-down already exists.** The `flow://orgs/{id}/nodes/{id}/complete` resource gives full structured detail for any single node. The tree overview tells the agent *where* to drill, not *everything* about each node.

4. **Proven pattern.** The existing `topology_summary` field on `complete` responses already renders connections as one-line text (e.g., `"API Gateway → Auth Service. Model Router → [Inference Engine, Embeddings Service]."`). This extends that pattern to the full org.

### Example output

For an org modeling OpenAI's software systems:

```
OpenAI (10 services)
├── API Platform — Developer-facing API (13 services)
│   ├── API Gateway → Auth Service → Rate Limiter → Model Router
│   ├── Model Router → Inference Engine (completions) → Streaming Service (SSE)
│   ├── Model Router → Embeddings Service (embeddings)
│   ├── Model Router → Assistants Runtime (assistants) → File Storage (files)
│   ├── Model Router → Fine-tuning Service (fine-tune) → File Storage (datasets)
│   ├── Model Router → Realtime Service (realtime)
│   └── Content Filter, Billing & Metering (no connections)
├── ChatGPT — Consumer product (0 services)
├── Model Training — Distributed training infrastructure (0 services)
├── Safety & Alignment — AI safety research (0 services)
├── Infrastructure — Core compute platform (0 services)
├── Data Platform — Data pipelines (0 services)
├── Evaluation — Model benchmarking (0 services)
├── Trust & Security — Content moderation (0 services)
├── Research Platform — Experiment tracking (0 services)
└── Developer Experience — SDKs, docs, playground (0 services)
```

That's the entire org — 24 nodes, 11 connections, 3 levels of hierarchy — in ~20 lines. An agent can immediately understand the architecture and decide where to look deeper.

## Implementation Plan

### Layer 1: Backend API

**New service method** in `backend/src/core/services/node_service.py`:

`get_org_tree(org_id, user_id)` — builds a recursive tree by:
1. Fetching all nodes in the org (reuse existing `get_nodes_by_org_with_counts()`)
2. Fetching all parent-child edges (reuse existing `get_all_visual_property_edges_for_org()`)
3. Fetching all connections between children at each level (reuse existing `get_all_connections_for_org()`)
4. Assembling into a recursive tree rooted at the org's `root_node_id`

Returns a JSON tree structure:
```json
{
    "org": { "id": "...", "name": "...", "root_node_id": "..." },
    "tree": {
        "id": "...",
        "title": "Root",
        "description": "...",
        "children": [
            {
                "id": "...",
                "title": "API Platform",
                "description": "Developer-facing API...",
                "children": [ ... ],
                "connections": [
                    { "from": "API Gateway", "to": "Auth Service", "label": "" }
                ]
            }
        ],
        "connections": []
    },
    "total_nodes": 24,
    "max_depth": 2
}
```

**New route** in `backend/src/api/routes/nodes.py`:
```
GET /orgs/{org_id}/tree
```

### Layer 2: MCP Client

**New HTTP call** in `mcp/client/api.py`:
```python
async def get_org_tree(client, headers, org_id) -> httpx.Response:
    return await client.get(f"/orgs/{org_id}/tree", headers=headers)
```

**New wrapper** in `mcp/flow_client.py`:
```python
async def get_org_tree(self, org_id: str) -> dict:
    return await self._with_retry(api.get_org_tree, org_id)
```

### Layer 3: MCP Transform

**New function** in `mcp/client/transform.py`:

`transform_org_tree(tree_response)` — recursively converts the JSON tree into indented text:
- Each node: `title — description (N services)`
- Each node's connections rendered as chains: `A → B (label) → C`
- Unconnected children listed as: `NodeA, NodeB (no connections)`
- Tree-drawing characters: `├──`, `└──`, `│`

### Layer 4: MCP Resource

**New resource** in `mcp/resources/nodes.py`:
```python
@server.resource("flow://orgs/{org_id}/tree")
async def get_org_tree(org_id: str) -> str:
    """Full recursive tree of an org — optimized for AI agent onboarding.

    Returns a compact indented text overview showing every node,
    their hierarchy, and how they connect at each level.
    Use this to understand an org's architecture before drilling
    into specific nodes with flow://orgs/{org_id}/nodes/{node_id}/complete.
    """
    raw = await _client().get_org_tree(org_id)
    return transform_org_tree(raw)
```

## Files to modify

| File | Change |
|------|--------|
| `backend/src/core/services/node_service.py` | Add `get_org_tree()` method |
| `backend/src/api/routes/nodes.py` | Add `GET /orgs/{org_id}/tree` route |
| `mcp/client/api.py` | Add `get_org_tree()` HTTP call |
| `mcp/flow_client.py` | Add `get_org_tree()` client wrapper |
| `mcp/client/transform.py` | Add `transform_org_tree()` text formatter |
| `mcp/resources/nodes.py` | Add `flow://orgs/{org_id}/tree` resource |

## Existing code to reuse

All the data we need is already queryable — no new database tables or projections required:

- **`node_service.get_nodes_by_org_with_counts()`** — all nodes with parent/child/descendant counts
- **`projection_repo.get_all_visual_property_edges_for_org()`** — all parent→child edges
- **`projection_repo.get_all_connections_for_org()`** — all connection edges with labels
- **`transform._build_topology_summary()`** — pattern for rendering connections as arrow chains
- **`node_service.get_org_graph()`** — closely related method to follow as a structural template

## Verification

1. Hit `GET /orgs/{org_id}/tree` for the OpenAI org — confirm JSON tree has root → 10 departments → API Platform's 13 services
2. Read `flow://orgs/{org_id}/tree` via MCP — confirm indented text output renders the full hierarchy with connections inline
3. Verify text output is under ~5k tokens for the 24-node OpenAI org
4. Test with an empty org (just root node) — should return a single line
