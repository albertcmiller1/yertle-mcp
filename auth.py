"""Cognito JWT validation and OAuth metadata for production deployment.

In production (Lambda), validates RS256 JWTs from the Cognito User Pool.
JWKS keys are cached at module level so they persist across warm Lambda invocations.
"""

import os
import logging

import httpx
from jose import jwt, JWTError

logger = logging.getLogger("yertle")

COGNITO_REGION = os.environ.get("COGNITO_REGION", "us-east-1")
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_APP_CLIENT_ID = os.environ.get("COGNITO_APP_CLIENT_ID", "")

JWKS_URL = (
    f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com"
    f"/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
)
ISSUER = (
    f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com"
    f"/{COGNITO_USER_POOL_ID}"
)

# Module-level cache — survives across warm Lambda invocations
_jwks_cache: dict | None = None


async def _get_jwks() -> dict:
    """Fetch and cache JWKS keys from Cognito."""
    global _jwks_cache
    if _jwks_cache is None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(JWKS_URL)
            resp.raise_for_status()
            _jwks_cache = resp.json()
        logger.info("JWKS keys fetched and cached")
    return _jwks_cache


async def validate_token(token: str) -> dict:
    """Validate a Cognito JWT and return the decoded claims.

    Validates signature (RS256 via JWKS) and issuer, but skips audience check
    because DCR-created clients have different client IDs and Cognito access
    tokens don't include an aud claim.

    Returns dict with at least 'sub' (user ID) claims.
    Raises RuntimeError on invalid/expired tokens.
    """
    jwks = await _get_jwks()
    try:
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            issuer=ISSUER,
            options={"verify_aud": False},
        )
        return claims
    except JWTError as e:
        raise RuntimeError(f"Token validation failed: {e}")


def _get_jwks_sync() -> dict:
    """Fetch and cache JWKS keys from Cognito (synchronous)."""
    global _jwks_cache
    if _jwks_cache is None:
        import httpx as httpx_sync
        resp = httpx_sync.get(JWKS_URL)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        logger.info("JWKS keys fetched and cached (sync)")
    return _jwks_cache


def validate_token_sync(token: str) -> dict:
    """Validate a Cognito JWT synchronously. Returns decoded claims."""
    jwks = _get_jwks_sync()
    try:
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            issuer=ISSUER,
            options={"verify_aud": False},
        )
        return claims
    except JWTError as e:
        raise RuntimeError(f"Token validation failed: {e}")


def oauth_metadata(resource_url: str, cognito_domain: str) -> dict:
    """Return the OAuth Protected Resource metadata document.

    Served at GET /.well-known/oauth-protected-resource per RFC 9470.
    """
    return {
        "resource": resource_url,
        "authorization_servers": [cognito_domain],
    }
