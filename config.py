"""Environment-based configuration for the Flow MCP server.

Supports two modes:
  - Local (stdio): reads FLOW_USER_EMAIL / FLOW_USER_PASSWORD for auth
  - Lambda (production): user authenticates via OAuth; no credentials needed here
"""

import os


def is_lambda() -> bool:
    """Return True if running inside AWS Lambda."""
    return "AWS_LAMBDA_FUNCTION_NAME" in os.environ


class Config:
    """Reads configuration from environment variables.

    Local mode env vars:
        FLOW_API_URL        — Base URL of the Flow backend (default: http://localhost:8000)
        FLOW_USER_EMAIL     — Email for local auth sign-in
        FLOW_USER_PASSWORD  — Password for local auth sign-in
        FLOW_DEFAULT_ORG_ID — Default org UUID (optional)

    Lambda mode env vars:
        FLOW_API_URL        — Backend API URL (e.g. https://api-blue-dev.albertcmiller.com)
        COGNITO_REGION      — AWS region for Cognito
        COGNITO_USER_POOL_ID    — Cognito User Pool ID
        COGNITO_APP_CLIENT_ID   — OAuth app client ID
        FLOW_DEFAULT_ORG_ID     — Default org UUID (optional)
    """

    def __init__(self):
        self._api_url = os.environ.get("FLOW_API_URL", "http://localhost:8000")
        self._default_org_id = os.environ.get("FLOW_DEFAULT_ORG_ID")
        self._is_lambda = is_lambda()

        if self._is_lambda:
            # In Lambda mode, user tokens are set per-request via OAuth.
            # No email/password needed.
            self._user_email = None
            self._user_password = None
        else:
            self._user_email = os.environ.get("FLOW_USER_EMAIL")
            if not self._user_email:
                raise RuntimeError("FLOW_USER_EMAIL environment variable is required")

            self._user_password = os.environ.get("FLOW_USER_PASSWORD")
            if not self._user_password:
                raise RuntimeError("FLOW_USER_PASSWORD environment variable is required")

    @property
    def api_url(self) -> str:
        """Base URL of the Flow REST API (e.g. http://localhost:8000)."""
        return self._api_url

    @property
    def is_lambda(self) -> bool:
        """True if running inside AWS Lambda."""
        return self._is_lambda

    @property
    def user_email(self) -> str | None:
        """Email address used to sign in (local mode only)."""
        return self._user_email

    @property
    def user_password(self) -> str | None:
        """Password used to sign in (local mode only)."""
        return self._user_password

    @property
    def default_org_id(self) -> str | None:
        """Default organization UUID, or None if not configured."""
        return self._default_org_id

    def resolve_org_id(self, org_id: str | None) -> str:
        """Return org_id if provided, otherwise fall back to default_org_id.

        Raises a ValueError with a helpful message if neither is available,
        guiding the LLM to use the flow://orgs resource to discover org IDs.
        """
        resolved = org_id or self._default_org_id
        if not resolved:
            raise ValueError(
                "org_id is required. Set FLOW_DEFAULT_ORG_ID or pass org_id explicitly. "
                "Use the flow://orgs resource to find available orgs."
            )
        return resolved
