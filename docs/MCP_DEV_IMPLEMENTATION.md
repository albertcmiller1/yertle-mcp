# MCP Server — Dev/Prod Deployment Implementation

> This document captures the complete implementation for deploying the yertle MCP server to AWS. It reflects the working code as of April 2026.

## Overview

The MCP server runs as an AWS Lambda function behind the existing API Gateway, accessible over HTTPS. Users authenticate via OAuth 2.1 (authorization code flow with PKCE) through Cognito's hosted UI. The server uses FastMCP in stateless HTTP mode, wrapped by Mangum for Lambda compatibility.

**Deployed and tested on:** claude.ai, Claude Desktop, Claude Code

## Key Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Auth method | OAuth 2.1 + PKCE (authorization code flow) | MCP spec standard; natively supported by Claude clients |
| OAuth client naming | Generic `OAuth*` (not `MCP*`) | Same Cognito client reused by CLI browser login (future) |
| Cognito domain | Custom domain (`auth.albertcmiller.com` / `auth.yertle.com`) | Professional, branded login experience |
| API Gateway | Routes on existing backend API Gateway | Reuses infrastructure; avoids managing a separate gateway |
| Deployment | Bundled into existing `yertle/deployment/` pipeline | Pipeline is battle-tested; cross-stack dependencies already wired |
| User identity | Pass through user's OAuth token to Flow API | Each MCP user acts as themselves, not a shared service account |
| DCR strategy | Return pre-registered `OAuthUserPoolClient` ID | Ensures token audience matches Flow API Gateway; no dynamic client creation |
| Lambda mode | Stateless HTTP, fresh session manager per invocation | Required because Mangum triggers ASGI lifespan per-invocation |

## Architecture

```
Claude Desktop / claude.ai / Claude Code
        │
        │  1. POST /mcp → 401 + WWW-Authenticate (resource_metadata URL)
        │  2. GET /.well-known/oauth-protected-resource → our server as auth server
        │  3. GET /.well-known/oauth-authorization-server → Cognito endpoints + DCR
        │  4. POST /register → returns pre-registered OAuthUserPoolClient ID
        │  5. Browser → auth.albertcmiller.com (Cognito hosted UI) → user logs in
        │  6. Cognito redirects to callback with auth code
        │  7. Client exchanges code for tokens (PKCE, directly with Cognito)
        │  8. POST /mcp + Bearer token → MCP tool calls work
        ▼
┌─────────────────────────────────────────────────────────┐
│  API Gateway (existing: flow-api-{env})                 │
│                                                         │
│  Existing backend routes ──► Backend Lambda             │
│    GET /health (no auth)                                │
│    POST /auth/signin (no auth)                          │
│    POST /auth/refresh (no auth)                         │
│    ANY /{proxy+} (CognitoAuthorizer — accepts both      │
│                   frontend + OAuth client audiences)     │
│                                                         │
│  MCP routes ──► MCP Lambda                              │
│    POST /mcp (no API Gateway auth — Lambda handles it)  │
│    GET /mcp (no API Gateway auth — Lambda handles it)   │
│    GET /.well-known/oauth-protected-resource (no auth)  │
│    GET /.well-known/oauth-authorization-server (no auth)│
│    POST /register (no auth)                             │
└─────────────────────────────────────────────────────────┘
        │                              │
        ▼                              ▼
  Backend Lambda                 MCP Lambda
  (existing)                     ┌────────────────────┐
                                 │ TokenPassthrough    │
                                 │   Middleware:       │
                                 │  - OAuth metadata   │
                                 │  - Auth server meta │
                                 │  - DCR (returns     │
                                 │    pre-reg client)  │
                                 │  - Token validation │
                                 │  - Token passthrough│
                                 │  - 401 + WWW-Auth   │
                                 │                     │
                                 │ FastMCP (stateless) │
                                 │  - 8 Tools          │
                                 │  - 4 Resources      │
                                 └─────────┬───────────┘
                                           │ HTTP with user's
                                           │ Bearer token
                                           ▼
                                     Backend API
                                   (same gateway,
                                    CognitoAuthorizer
                                    accepts OAuth
                                    client audience)
```

## How Authentication Works (Full Detail)

This section explains the complete auth flow, including the problems we encountered and how they were solved. Understanding this is critical for debugging.

### The MCP OAuth Requirement

The MCP spec requires remote servers to support:
1. **Protected Resource Metadata** (`/.well-known/oauth-protected-resource`) — RFC 9728
2. **Authorization Server Metadata** (`/.well-known/oauth-authorization-server`) — RFC 8414
3. **Dynamic Client Registration** (`POST /register`) — RFC 7591
4. **OAuth 2.1 authorization code flow with PKCE**

AWS Cognito does NOT natively support items 2 and 3. Our MCP Lambda serves these endpoints itself, acting as an OAuth adapter layer in front of Cognito.

### The DCR Problem and Solution

**Problem:** The MCP spec says clients must register via DCR before starting the OAuth flow. The naive approach was to call `cognito-idp:CreateUserPoolClient` for each registration, creating a unique Cognito app client per MCP client. This produced tokens with different `client_id` claims. The Flow backend API Gateway's JWT authorizer has an `Audience` list that checks the token's `client_id` — it rejected tokens from dynamically created clients because their `client_id` wasn't in the list.

**Solution:** The DCR endpoint (`POST /register`) returns the **pre-registered `OAuthUserPoolClient` ID** for all registrants instead of creating new clients. This means:
- All MCP clients get the same `client_id`
- All tokens have `client_id` matching the `OAuthUserPoolClient`
- The Flow API Gateway's `CognitoAuthorizer` accepts them (audience list includes both the original frontend client AND the OAuth client)
- Each user still authenticates as themselves via Cognito — they share one app client but have individual user identities

### The Token Pass-Through Flow

```
1. claude.ai sends POST /mcp with Authorization: Bearer <token>
2. TokenPassthroughMiddleware intercepts:
   a. Extracts Bearer token from Authorization header
   b. Validates JWT signature (RS256 via Cognito JWKS)
   c. Validates issuer (must match our Cognito User Pool)
   d. Skips audience check (not needed — issuer is sufficient)
   e. Calls client.set_user_token(token) on the shared FlowClient
   f. If validation fails → returns 401 with WWW-Authenticate header
3. FastMCP handles the MCP request, invokes the tool
4. Tool calls FlowClient method (e.g., list_organizations)
5. FlowClient sends HTTP request to Flow backend API with the user's token
6. Flow API Gateway validates the token:
   - Checks issuer (Cognito User Pool) ✓
   - Checks audience (client_id in token matches OAuthUserPoolClient) ✓
7. Flow backend processes the request as the authenticated user
8. Response flows back through FlowClient → tool → FastMCP → Mangum → API Gateway → client
```

### Why Auth Validation Skips Audience

Cognito access tokens contain a `client_id` claim (not `aud`). API Gateway v2's JWT authorizer checks `client_id` against its `Audience` list. But our MCP Lambda's own token validation (`auth.py`) runs BEFORE the token reaches the Flow API Gateway. We validate issuer + signature but skip `aud`/`client_id` because:
- The issuer check proves the token came from our Cognito pool
- The `client_id` check happens downstream at the Flow API Gateway
- Checking audience in our middleware would require knowing all valid client IDs

### The Backend API Gateway Audience Fix

The Flow backend API Gateway's `CognitoAuthorizer` originally only accepted tokens from the frontend app client (`CognitoUserPoolClientId`). We added the `OAuthUserPoolClientId` to its `Audience` list so it accepts tokens from MCP clients too:

```yaml
# In api-core.yml
CognitoAuthorizer:
  JwtConfiguration:
    Audience:
      - !Ref CognitoUserPoolClientId      # frontend/CLI
      - !Ref OAuthUserPoolClientId         # MCP clients
```

## Lambda Lifecycle (Critical for Debugging)

Running FastMCP inside Lambda required solving several lifecycle issues:

### Problem 1: Session Manager Can Only Start Once

FastMCP's `StreamableHTTPSessionManager.run()` creates an async task group and can only be called once per instance. But Mangum triggers the ASGI lifespan on every invocation.

**Solution:** In `lambda_handler.py`, reset `server._session_manager = None` before each invocation so `streamable_http_app()` creates a fresh one:

```python
def handler(event, context):
    server._session_manager = None
    app = server.streamable_http_app()
    ...
```

### Problem 2: Lifespan Creates Duplicate FlowClient

The `lifespan()` function in `app.py` creates a new `Config` and `FlowClient`, storing them on `server._flow_client`. But `_init_lambda()` already creates these on cold start. When the lifespan runs (triggered by Mangum), it overwrites `server._flow_client` with a NEW instance — and the middleware's `set_user_token()` call targets the OLD instance.

**Solution:** The lifespan is a no-op in Lambda mode:

```python
@asynccontextmanager
async def lifespan(server: FastMCP):
    if is_lambda():
        yield
        return
    # local mode: create Config + FlowClient as before
    ...
```

### Problem 3: Stateless HTTP Mode

MCP clients send `initialize`, `tools/list`, and `tools/call` as separate HTTP requests. FastMCP's default session mode expects these to be part of the same session, but Lambda processes each request independently.

**Solution:** Enable `stateless_http=True` in Lambda mode:

```python
server = FastMCP(
    "yertle",
    stateless_http=True if is_lambda() else False,
    ...
)
```

### Problem 4: DNS Rebinding Protection

FastMCP's default transport security only allows `localhost` hosts. Lambda receives requests from the API Gateway domain.

**Solution:** Disable DNS rebinding protection in Lambda mode:

```python
_transport_security = (
    TransportSecuritySettings(enable_dns_rebinding_protection=False)
    if is_lambda()
    else None
)
```

### Problem 5: API Gateway Stage Prefix

API Gateway v2 with a custom domain includes the stage name in the path (e.g., `/dev/mcp`). FastMCP registers its route at `/mcp` (exact match). The middleware uses `.endswith()` for its own routes, but FastMCP's Starlette router does exact matching.

**Solution:** Configure Mangum to strip the stage prefix:

```python
stage = os.environ.get("ENVIRONMENT", "dev")
mangum = Mangum(app, lifespan="auto", api_gateway_base_path=f"/{stage}")
```

### Problem 6: Python Version Mismatch in Build

`pip install` on macOS downloads macOS wheels. Lambda runs Amazon Linux. Additionally, the local Python version (3.12) differs from the Lambda runtime (3.13), so pip downloads the wrong platform's compiled extensions (e.g., `pydantic_core`).

**Solution:** The consolidated build script passes `--platform` and `--python-version`:

```bash
pip install --target ${BUILD_DIR} -r ${REQUIREMENTS_FILE} \
    --platform manylinux2014_x86_64 --python-version ${PYTHON_VERSION} \
    --only-binary=:all: --quiet
```

## Complete File List

### yertle-mcp (MCP server code)

| File | Purpose |
|------|---------|
| `lambda_handler.py` | Lambda entry point. Resets session manager, creates fresh ASGI app per invocation, wraps with Mangum. |
| `app.py` | FastMCP server setup, lifespan, `TokenPassthroughMiddleware` (OAuth metadata, DCR, token validation, 401 handling). |
| `auth.py` | Cognito JWT validation (RS256 via JWKS). Caches keys at module level across warm invocations. |
| `config.py` | Environment config. Detects Lambda mode via `AWS_LAMBDA_FUNCTION_NAME`. |
| `flow_client.py` | HTTP client for Flow API. In Lambda mode, token set per-request via `set_user_token()`. |
| `requirements.txt` | `mcp`, `httpx`, `mangum`, `python-jose[cryptography]` |
| `tools/org.py` | `list_organizations` and `create_organization` tools |
| `tools/node.py` | `create_node`, `push_node_state`, `delete_node`, `search_nodes`, `create_branch`, `list_branches` tools |
| `resources/orgs.py` | `flow://orgs` resource |
| `resources/nodes.py` | `flow://orgs/{org_id}/nodes`, `.../complete`, `.../canvas` resources |

### yertle (deployment infrastructure)

| File | Purpose |
|------|---------|
| `deployment/infrastructure/shared/cognito.yml` | Cognito User Pool + `UserPoolDomain` (custom domain) + `OAuthUserPoolClient` (shared OAuth client) |
| `deployment/infrastructure/backend/mcp-lambda.yml` | MCP Lambda function (Python 3.13, 256MB, 30s timeout) |
| `deployment/infrastructure/backend/api-core.yml` | API Gateway with MCP routes, two JWT authorizers (frontend + OAuth), MCP Lambda integration |
| `deployment/hooks/backend/build-versioned-package.sh` | Consolidated build script for both backend and MCP Lambda packages. Handles platform targeting. |
| `deployment/dev.yaml` | Pipeline config with `build_mcp_package` hook, `mcp_lambda` CFT, and updated `apigateway` params |

## API Gateway Routes (Complete)

| Route | Target | Auth | Purpose |
|-------|--------|------|---------|
| `GET /health` | Backend Lambda | NONE | Health check |
| `POST /contact-messages` | Backend Lambda | NONE | Contact form |
| `POST /auth/signin` | Backend Lambda | NONE | Email/password → JWT |
| `POST /auth/refresh` | Backend Lambda | NONE | Refresh token |
| `OPTIONS /{proxy+}` | Backend Lambda | NONE | CORS preflight |
| `ANY /{proxy+}` | Backend Lambda | CognitoAuthorizer | All other backend API calls |
| `POST /mcp` | MCP Lambda | NONE* | MCP JSON-RPC (auth handled by Lambda) |
| `GET /mcp` | MCP Lambda | NONE* | MCP SSE |
| `GET /.well-known/oauth-protected-resource` | MCP Lambda | NONE | Protected resource metadata |
| `GET /.well-known/oauth-authorization-server` | MCP Lambda | NONE | Authorization server metadata |
| `POST /register` | MCP Lambda | NONE | Dynamic client registration |

*MCP routes have `AuthorizationType: NONE` on API Gateway. The MCP Lambda handles auth itself so it can return proper `WWW-Authenticate` headers per the MCP spec.

## Lambda Environment Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| `ENVIRONMENT` | `dev` / `prod` | Environment name (used as API Gateway stage prefix) |
| `FLOW_API_URL` | `https://api-{slot}-{env}.{domain}` | Backend API URL for proxied tool calls |
| `COGNITO_REGION` | `us-east-1` | Cognito region for JWKS URL construction |
| `COGNITO_USER_POOL_ID` | From Cognito stack output | User Pool ID for issuer validation |
| `COGNITO_APP_CLIENT_ID` | From Cognito stack output (`OAuthUserPoolClientId`) | Returned by DCR endpoint |
| `COGNITO_DOMAIN` | `https://auth.{domain}` | Cognito hosted UI domain for OAuth metadata |

## Verification

```bash
# 1. OAuth metadata
curl https://api-blue-dev.albertcmiller.com/.well-known/oauth-protected-resource
curl https://api-blue-dev.albertcmiller.com/.well-known/oauth-authorization-server

# 2. Dynamic client registration (should return pre-registered client_id)
curl -X POST https://api-blue-dev.albertcmiller.com/register \
  -H "Content-Type: application/json" \
  -d '{"client_name":"test","redirect_uris":["https://example.com/callback"]}'

# 3. MCP endpoint (should return 401 with WWW-Authenticate)
curl -X POST https://api-blue-dev.albertcmiller.com/mcp

# 4. MCP with token (should return tool results)
TOKEN=$(curl -s -X POST https://api-blue-dev.albertcmiller.com/auth/signin \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"Test123!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['accessToken'])")

curl -X POST https://api-blue-dev.albertcmiller.com/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_organizations","arguments":{}},"id":1}'

# 5. claude.ai: Settings > Connectors > Add custom connector
#    Name: Yertle Dev
#    URL: https://api-blue-dev.albertcmiller.com/mcp
```

## Future: CLI Browser-Based Login

The OAuth infrastructure (Cognito hosted UI, `OAuthUserPoolClient`) is designed to be reused by `yertle-cli` for browser-based login. See `yertle-cli/docs/OAUTH_BROWSER_LOGIN.md`.

The `OAuthUserPoolClient` already has `http://localhost:9876/callback` in its callback URLs. Only CLI-side Go code changes needed.

## Future: Other Considerations

- **Provisioned concurrency:** Cold starts are 1.5-2s. Add provisioned concurrency ($11/month) or CloudWatch ping schedule if noticeable.
- **Custom scopes:** Add `yertle:read`, `yertle:write` OAuth scopes for fine-grained permissions.
- **Per-user audit trail:** Currently all MCP users share one Cognito app client. Audit logs show the user's `sub` (Cognito user ID) but the same `client_id`. If per-client tracking is needed, consider Client ID Metadata Documents (CIMD) per the Nov 2025 MCP spec.
- **Resources in claude.ai:** claude.ai connectors only support tools, not MCP resources. The `list_organizations` tool was added as a workaround. Other resources may need tool equivalents.
- **Debug logging:** Lambda logging goes to CloudWatch. The `logging` module writes to stderr (for stdio transport compatibility). Use `print()` for Lambda-visible logs during debugging.
