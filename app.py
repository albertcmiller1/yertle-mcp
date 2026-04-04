"""Yertle MCP Server — app setup and shared objects.

Creates the FastMCP server instance, lifespan, and helper accessors.
Tools and resources are registered by importing the tools/ and resources/ packages.
"""

import logging
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from config import Config
from flow_client import FlowClient

# Log to stderr so it doesn't interfere with stdio MCP transport
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("yertle")


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Connect to the Flow backend on startup, disconnect on shutdown."""
    config = Config()
    client = FlowClient(config)
    await client.connect()

    server._flow_config = config
    server._flow_client = client

    yield

    await client.close()


server = FastMCP(
    "yertle",
    instructions=(
        "Interact with the Yertle visual workspace — create architecture diagrams, "
        "manage nodes, and push state changes. "
        "LAYOUT: Always arrange diagrams left-to-right (data flows from left to right). "
        "Increase position_x to move right, position_y to move down. "
        "Place sources/entry points on the left, sinks/endpoints on the right. "
        "For parallel services at the same stage, stack them vertically (same x, different y). "
        "VIEWPORT: The entire diagram is encouraged to fit within 1200px wide by 800px tall "
        "(a laptop screen at 100% zoom), though it's fine to go over if there are many nodes. "
        "Use 200x100 nodes, 250px horizontal spacing, 130px vertical spacing. "
        "Prefer compact layouts over spread-out ones. "
        "RESOURCES: Most resources require an org_id. If you don't know the org, "
        "ask the user or use flow://orgs to list available organizations first. "
        "To inspect a node's architecture, use flow://orgs/{org_id}/nodes/{node_id}/complete. "
        "VISUALIZATION: When describing a node that has child components and connections, "
        "render an ASCII box-and-arrow diagram showing the architecture layout. "
        "Use Unicode box-drawing characters for clean rendering in the terminal. "
        "EDITING: When adding nodes to an existing diagram, only include new nodes "
        "in visual_properties. Never reposition existing nodes unless the user "
        "explicitly asks to rearrange or redesign the layout."
    ),
    lifespan=lifespan,
)


def _client() -> FlowClient:
    """Get the FlowClient from the server (set during lifespan)."""
    return server._flow_client


def _config() -> Config:
    """Get the Config from the server (set during lifespan)."""
    return server._flow_config
