"""Async HTTP client wrapping the Flow REST API.

This is the main client class that orchestrates auth, API calls,
response transformation, and push logic. The heavy lifting is
delegated to api.py, transform.py, and push.py.

Supports two auth modes:
  - Local: signs in with email/password, manages token refresh
  - Lambda: passes through the user's OAuth Bearer token
"""

import json
import logging
import httpx

from config import Config
from client import api
from client.push import prepare_push_state

logger = logging.getLogger("yertle")


class FlowClient:
    """Async HTTP client for the Flow REST API.

    Usage (local mode):
        client = FlowClient(config)
        await client.connect()       # signs in + creates httpx client
        nodes = await client.list_nodes(org_id)
        await client.close()

    Usage (Lambda mode):
        client = FlowClient(config)
        await client.connect()       # creates httpx client only (no sign-in)
        client.set_user_token(token)  # set per-request OAuth token
        nodes = await client.list_nodes(org_id)
    """

    def __init__(self, config: Config):
        """Store config. The httpx client is created in connect()."""
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None

    async def connect(self):
        """Create the httpx.AsyncClient and optionally sign in.

        In local mode, signs in with email/password to get tokens.
        In Lambda mode, tokens are set per-request via set_user_token().
        """
        self._client = httpx.AsyncClient(
            base_url=self._config.api_url,
            timeout=30.0,
        )
        if not self._config.is_lambda:
            await self._sign_in()

    async def close(self):
        """Close the underlying httpx client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Auth
    # ------------------------------------------------------------------ #

    def set_user_token(self, token: str) -> None:
        """Set the user's OAuth Bearer token for pass-through (Lambda mode).

        In production, the MCP server receives a pre-validated JWT from
        API Gateway and passes it through to the Flow API. Each user
        operates as themselves.
        """
        self._access_token = token

    async def _sign_in(self) -> None:
        """POST /auth/signin with email + password. Stores access + refresh tokens."""
        response = await self._client.post(
            "/auth/signin",
            json={
                "email": self._config.user_email,
                "password": self._config.user_password,
            },
        )
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            raise RuntimeError(f"Sign-in failed: {detail}")

        data = response.json()
        self._access_token = data["accessToken"]
        self._refresh_token = data.get("refreshToken")
        logger.info("Signed in successfully")

    async def _refresh(self) -> None:
        """POST /auth/refresh to get a new access token. Falls back to full sign-in."""
        if not self._refresh_token:
            logger.info("No refresh token, performing full sign-in")
            await self._sign_in()
            return

        response = await self._client.post(
            "/auth/refresh",
            json={"refreshToken": self._refresh_token},
        )
        if response.status_code != 200:
            logger.warning("Token refresh failed, performing full sign-in")
            await self._sign_in()
            return

        data = response.json()
        self._access_token = data["accessToken"]
        logger.info("Token refreshed successfully")

    def _headers(self) -> dict[str, str]:
        """Return Authorization header using the stored access token."""
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _with_retry(self, api_call, *args, **kwargs):
        """Call an api function with auto-retry on 401 (expired token).

        In Lambda mode, skip retry — the token is managed by the OAuth client,
        not by us. A 401 means the user's token is invalid.
        """
        resp = await api_call(self._client, self._headers(), *args, **kwargs)
        if resp.status_code == 401 and not self._config.is_lambda:
            logger.info("Got 401, refreshing token and retrying")
            await self._refresh()
            resp = await api_call(self._client, self._headers(), *args, **kwargs)
        return self._handle_response(resp)

    # ------------------------------------------------------------------ #
    # Write operations
    # ------------------------------------------------------------------ #

    async def create_organization(self, name: str) -> dict:
        """Create a new organization."""
        return await self._with_retry(api.create_organization, name)

    async def create_node(
        self,
        org_id: str,
        title: str,
        description: str | None = None,
        tags: dict | None = None,
        directories: list[str] | None = None,
        public_id: str | None = None,
    ) -> dict:
        """Create a new node in an organization."""
        return await self._with_retry(
            api.create_node, org_id, title,
            description=description, tags=tags, directories=directories,
            public_id=public_id,
        )

    async def push_state(
        self,
        org_id: str,
        node_id: str,
        message: str,
        state: dict,
        branch: str = "main",
    ) -> dict:
        """Read-modify-write push: fetch current state, merge, validate, and push."""
        current_raw = await self.get_complete_state(org_id, node_id)
        expected_head_commit = current_raw.get("_branch_context", {}).get("commit", "")
        merged = prepare_push_state(current_raw, state)

        logger.debug(
            "push_state node_id=%s merged_state=%s",
            node_id, json.dumps(merged, indent=2, default=str),
        )

        return await self._with_retry(
            api.push_raw, org_id, node_id, branch, message, merged, expected_head_commit,
        )

    async def delete_node(self, org_id: str, node_id: str) -> dict:
        """Permanently delete a node."""
        return await self._with_retry(api.delete_node, org_id, node_id)

    # ------------------------------------------------------------------ #
    # Read operations
    # ------------------------------------------------------------------ #

    async def list_organizations(self) -> dict:
        """List all orgs the current user belongs to."""
        return await self._with_retry(api.list_organizations)

    async def list_nodes(self, org_id: str, limit: int = 25, offset: int = 0) -> dict:
        """List nodes in an org (paginated)."""
        return await self._with_retry(api.list_nodes, org_id, limit=limit, offset=offset)

    async def search_nodes(self, org_id: str, filters: dict) -> dict:
        """Search nodes in an org with filters."""
        return await self._with_retry(api.search_nodes, org_id, filters)

    async def create_branch(
        self, org_id: str, node_id: str, name: str, base_branch: str = "main",
    ) -> dict:
        """Create a branch on a node."""
        return await self._with_retry(
            api.create_branch, org_id, node_id, name, base_branch,
        )

    async def list_branches(self, org_id: str, node_id: str) -> dict:
        """List branches on a node."""
        return await self._with_retry(api.list_branches, org_id, node_id)

    async def get_complete_state(self, org_id: str, node_id: str) -> dict:
        """Get the raw complete state for a node on main branch."""
        return await self._with_retry(api.get_complete_state, org_id, node_id)

    async def get_canvas_state(self, org_id: str, node_id: str) -> dict:
        """Get the raw canvas state for a node on main branch."""
        return await self._with_retry(api.get_canvas_state, org_id, node_id)

    # ------------------------------------------------------------------ #
    # Error handling
    # ------------------------------------------------------------------ #

    def _handle_response(self, response: httpx.Response) -> dict:
        """Check HTTP response and return JSON, or raise with a helpful message."""
        if 200 <= response.status_code < 300:
            return response.json()

        try:
            body = response.json()
            detail = body.get("detail", response.text)
        except Exception:
            detail = response.text

        status = response.status_code

        if status == 401:
            raise RuntimeError(
                f"Authentication failed: {detail}. "
                "Check your credentials or OAuth token."
            )
        elif status == 404:
            raise RuntimeError(f"Not found: {detail}. Check the ID you provided.")
        elif status == 422:
            raise RuntimeError(f"Validation error: {detail}")
        elif status >= 500:
            raise RuntimeError(
                f"Flow backend error ({status}): {detail}. Is the server running?"
            )
        else:
            raise RuntimeError(f"HTTP {status}: {detail}")
