"""Environment-based configuration for the Flow MCP server."""

import os


class Config:
    """Reads configuration from environment variables.

    Required env vars:
        FLOW_API_URL        — Base URL of the Flow backend (default: http://localhost:8000)
        FLOW_USER_EMAIL     — Email for local auth sign-in
        FLOW_USER_PASSWORD  — Password for local auth sign-in

    Optional env vars:
        FLOW_DEFAULT_ORG_ID — Default org UUID (avoids passing org_id on every tool call)
    """

    def __init__(self):
        self._api_url = os.environ.get("FLOW_API_URL", "http://localhost:8000")
        self._default_org_id = os.environ.get("FLOW_DEFAULT_ORG_ID")

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
    def user_email(self) -> str:
        """Email address used to sign in to the Flow backend."""
        return self._user_email

    @property
    def user_password(self) -> str:
        """Password used to sign in to the Flow backend."""
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
