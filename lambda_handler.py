"""AWS Lambda entry point — Mangum wraps the FastMCP ASGI app for Lambda.

Each Lambda invocation resets the session manager and creates a fresh
Starlette app because FastMCP's StreamableHTTPSessionManager can only
be started once per instance, and Mangum triggers the ASGI lifespan
on each invocation.
"""

import os

from mangum import Mangum

from app import server, TokenPassthroughMiddleware, _init_lambda
from config import is_lambda
import tools  # noqa: F401 — registers tool decorators
import resources  # noqa: F401 — registers resource decorators

# Initialize FlowClient once on cold start
if is_lambda():
    _init_lambda()

stage = os.environ.get("ENVIRONMENT", "dev")


def handler(event, context):
    """Create a fresh ASGI app per invocation to avoid session manager reuse."""
    # Reset the session manager so streamable_http_app() creates a new one
    server._session_manager = None

    app = server.streamable_http_app()
    if is_lambda():
        app = TokenPassthroughMiddleware(app)
    mangum = Mangum(app, lifespan="auto", api_gateway_base_path=f"/{stage}")
    return mangum(event, context)
