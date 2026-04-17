"""Yertle MCP Server — app setup and shared objects.

Creates the FastMCP server instance, lifespan, and helper accessors.
Tools and resources are registered by importing the tools/ and resources/ packages.
"""

import logging
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from config import Config, is_lambda
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
    """Connect to the Flow backend on startup, disconnect on shutdown.

    In local mode, signs in with email/password.
    In Lambda mode, this is a no-op — _init_lambda() already set up the
    FlowClient on cold start. We must NOT create a new one here because
    the middleware sets the user's token on the existing instance.
    """
    if is_lambda():
        yield
        return

    config = Config()
    client = FlowClient(config)
    await client.connect()

    server._flow_config = config
    server._flow_client = client

    yield

    await client.close()


# In Lambda mode, disable DNS rebinding protection (host is API Gateway domain)
_transport_security = (
    TransportSecuritySettings(enable_dns_rebinding_protection=False)
    if is_lambda()
    else None
)

server = FastMCP(
    "yertle",
    transport_security=_transport_security,
    stateless_http=True if is_lambda() else False,
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


class TokenPassthroughMiddleware:
    """ASGI middleware that extracts the Bearer token from incoming requests
    and sets it on the FlowClient for pass-through to the Flow API.

    Also handles the /.well-known/oauth-protected-resource endpoint,
    which must return OAuth metadata before any auth is required.

    Only active in Lambda mode. In local mode, the FlowClient manages its own
    tokens via email/password sign-in.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")

            logger.info("TokenPassthroughMiddleware path=%s", path)

            # Handle OAuth discovery and registration (no auth required)
            if path.endswith("/.well-known/oauth-protected-resource"):
                await self._serve_protected_resource_metadata(scope, receive, send)
                return

            if path.endswith("/.well-known/oauth-authorization-server"):
                await self._serve_authorization_server_metadata(scope, receive, send)
                return

            if path.endswith("/register"):
                await self._handle_client_registration(scope, receive, send)
                return

            # For MCP routes, validate Bearer token and return proper 401 if missing
            if path.endswith("/mcp"):
                headers = dict(scope.get("headers", []))
                auth_header = headers.get(b"authorization", b"").decode()

                if not auth_header.lower().startswith("bearer "):
                    await self._serve_unauthorized(scope, receive, send)
                    return

                token = auth_header[7:]
                try:
                    from auth import validate_token_sync
                    validate_token_sync(token)
                    # Token is valid — pass it through to the FlowClient
                    # so API calls are made as this user
                    client = _client()
                    client.set_user_token(token)
                except Exception as e:
                    logger.warning("Token validation failed: %s", e)
                    await self._serve_unauthorized(scope, receive, send)
                    return
        await self.app(scope, receive, send)

    async def _serve_unauthorized(self, scope, receive, send):
        """Return 401 with WWW-Authenticate header per MCP spec."""
        import os

        flow_api_url = os.environ.get("FLOW_API_URL", "")
        metadata_url = f"{flow_api_url}/.well-known/oauth-protected-resource"

        body = b'{"error": "unauthorized"}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
                [b"www-authenticate",
                 f'Bearer resource_metadata="{metadata_url}"'.encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })

    async def _serve_protected_resource_metadata(self, scope, receive, send):
        """Respond with OAuth Protected Resource metadata (RFC 9728).

        Points to OUR server as the authorization server (not Cognito directly),
        because we serve the authorization server metadata and DCR endpoints.
        """
        import json
        import os

        flow_api_url = os.environ.get("FLOW_API_URL", "")

        metadata = {
            "resource": f"{flow_api_url}/mcp",
            "authorization_servers": [flow_api_url],
        }

        await self._send_json(send, 200, metadata)

    async def _serve_authorization_server_metadata(self, scope, receive, send):
        """Respond with OAuth Authorization Server metadata (RFC 8414).

        Returns standard metadata pointing authorize/token to Cognito hosted UI,
        and registration_endpoint to our DCR proxy.
        """
        import os

        flow_api_url = os.environ.get("FLOW_API_URL", "")
        cognito_domain = os.environ.get("COGNITO_DOMAIN", "")

        # If COGNITO_DOMAIN isn't set, derive from the user pool
        if not cognito_domain:
            cognito_region = os.environ.get("COGNITO_REGION", "us-east-1")
            cognito_user_pool_id = os.environ.get("COGNITO_USER_POOL_ID", "")
            # Fetch from Cognito OIDC discovery on first call
            cognito_domain = f"https://auth.{os.environ.get('DOMAIN', 'yertle.com')}"

        metadata = {
            "issuer": flow_api_url,
            "authorization_endpoint": f"{cognito_domain}/oauth2/authorize",
            "token_endpoint": f"{cognito_domain}/oauth2/token",
            "registration_endpoint": f"{flow_api_url}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["openid", "profile", "email"],
        }

        await self._send_json(send, 200, metadata)

    async def _handle_client_registration(self, scope, receive, send):
        """Handle Dynamic Client Registration (RFC 7591).

        Returns the pre-registered OAuthUserPoolClient ID for all registrants.
        This ensures all MCP tokens have the same client_id, which matches
        the Flow API Gateway's audience check. Each user still authenticates
        as themselves via Cognito — we just share one app client.
        """
        import json
        import os

        # Read request body
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        try:
            request_data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            await self._send_json(send, 400, {"error": "invalid_request"})
            return

        client_name = request_data.get("client_name", "mcp-client")
        redirect_uris = request_data.get("redirect_uris", [])
        client_id = os.environ.get("COGNITO_APP_CLIENT_ID", "")

        dcr_response = {
            "client_id": client_id,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

        await self._send_json(send, 201, dcr_response)

    async def _send_json(self, send, status, data):
        """Helper to send a JSON response."""
        import json

        body = json.dumps(data).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })


def _init_lambda():
    """Initialize Config and FlowClient eagerly for Lambda mode.

    Called once on cold start from lambda_handler.py. In Lambda mode,
    we don't use the lifespan to create these — we do it at module load.
    """
    import asyncio

    async def _init():
        config = Config()
        client = FlowClient(config)
        await client.connect()
        server._flow_config = config
        server._flow_client = client

    asyncio.get_event_loop().run_until_complete(_init())


def get_streamable_http_app():
    """Return a fresh ASGI app, wrapped with token middleware in Lambda mode.

    Each call creates a new streamable_http_app() so the session manager
    is fresh (it can only be started once per instance).
    """
    app = server.streamable_http_app()
    if is_lambda():
        app = TokenPassthroughMiddleware(app)
    return app
