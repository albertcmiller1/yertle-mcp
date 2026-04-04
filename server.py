"""Yertle MCP Server — entrypoint.

Run: python mcp/server.py (stdio transport, spawned by Claude Code / Claude Desktop)

Importing tools and resources triggers their @server.tool() and @server.resource()
decorator registration on the shared server instance from app.py.
"""

from app import server  # noqa: F401 — server must be imported before tools/resources

import tools  # noqa: F401 — registers tool decorators
import resources  # noqa: F401 — registers resource decorators

if __name__ == "__main__":
    server.run(transport="stdio")
