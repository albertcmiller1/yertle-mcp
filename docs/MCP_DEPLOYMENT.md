# Flow MCP Server — Production Deployment

How to deploy the Flow MCP server to AWS as a fully serverless service using Lambda + API Gateway, with OAuth 2.1 authentication via Cognito.

---

## Architecture

```
┌──────────────┐         HTTPS          ┌──────────────┐                    ┌──────────────┐
│  Claude.ai / │ ────────────────────► │ API Gateway  │ ────────────────► │   Lambda     │
│  Claude      │                       │  /mcp        │                    │  (MCP handler)│
│  Desktop     │ ◄──────────────────── │              │ ◄──────────────── │              │
│              │                       └──────────────┘                    └──────┬───────┘
│              │                                                                  │
│              │       OAuth 2.1                                                  │ HTTPS
│              │ ◄─── browser redirect ──┐                                       │
│              │                         │                                        ▼
│              │                    ┌────┴────────┐                        ┌──────────────┐
│              │                    │  Cognito    │                        │  Flow API    │
│              │ ──── login ──────►│  User Pool  │                        │  (existing   │
│              │ ◄─── token ───── │             │                        │   Lambda)    │
└──────────────┘                    └─────────────┘                        └──────────────┘
```

**Fully serverless** — no EC2, no containers, no always-on costs. Same infra pattern as the existing Flow backend.

---

## Why Lambda Works for MCP

MCP's Streamable HTTP transport is regular HTTP request/response for tool calls:

```
POST /mcp  →  { "method": "tools/call", "params": { "name": "create_node", ... } }
           ←  { "result": { "id": "node-uuid", ... } }
```

Every MCP operation (create_node, push_state, get_complete_state) is a single HTTP POST → JSON response. No persistent connections, no WebSockets, no long-running streams. This is a perfect fit for Lambda.

---

## Components

### 1. Lambda Function

The MCP server packaged as a Lambda function. Uses **Mangum** (ASGI-to-Lambda adapter) to run the `mcp` SDK's Starlette-based HTTP server inside Lambda.

```python
# lambda_handler.py
from mangum import Mangum
from server import mcp  # our FastMCP server

# Mangum wraps the ASGI app for Lambda
handler = Mangum(mcp.asgi_app())
```

**Runtime:** Python 3.11+
**Memory:** 256 MB (lightweight — just HTTP proxying and JSON transformation)
**Timeout:** 30 seconds (matches upstream Flow API timeout)

### 2. API Gateway (HTTP API)

Routes MCP traffic to the Lambda function. Uses API Gateway **HTTP API** (v2), not REST API — cheaper and lower latency.

| Route | Method | Purpose |
|-------|--------|---------|
| `/mcp` | POST | MCP JSON-RPC messages (tool calls, resource reads) |
| `/mcp` | GET | MCP SSE endpoint (for server-initiated messages, if needed) |
| `/.well-known/oauth-protected-resource` | GET | OAuth metadata discovery |

**Custom domain:** `mcp.yertle.com` (via Route 53 + ACM certificate)

### 3. Cognito (OAuth 2.1)

Uses the **existing Cognito User Pool** with a new App Client dedicated to MCP.

#### New App Client Config

| Setting | Value |
|---------|-------|
| **App client name** | `flow-mcp-server` |
| **Allowed callback URLs** | `https://claude.ai/api/mcp/auth_callback` |
| **Allowed sign-out URLs** | `https://claude.ai` |
| **OAuth grant types** | Authorization code |
| **OAuth scopes** | `openid`, `profile` (custom scopes like `flow:read`, `flow:write` later) |
| **PKCE** | Required (S256) |

No new User Pool needed — same users, same passwords.

---

## OAuth Flow

1. Claude sends `POST /mcp` to API Gateway (unauthenticated)
2. Lambda responds with `401` + `WWW-Authenticate` header containing the metadata URL
3. Claude fetches `GET /.well-known/oauth-protected-resource` from API Gateway
4. Response points to Cognito as the authorization server:
   ```json
   {
     "resource": "https://mcp.yertle.com",
     "authorization_servers": [
       "https://cognito-idp.us-east-1.amazonaws.com/<user-pool-id>"
     ]
   }
   ```
5. Claude discovers Cognito's OAuth endpoints via `/.well-known/openid-configuration`
6. Claude opens a browser → user logs in via Cognito hosted UI
7. Cognito redirects to `https://claude.ai/api/mcp/auth_callback` with an auth code
8. Claude exchanges the code for tokens (with PKCE verification)
9. Subsequent requests include `Authorization: Bearer <access_token>`
10. Lambda validates the token against Cognito's JWKS endpoint, extracts the user ID, proxies to the Flow API

---

## Lambda Auth Layer

The Lambda function validates Cognito JWTs on every request:

```python
# auth.py
import httpx
from jose import jwt, JWTError

COGNITO_REGION = os.environ["COGNITO_REGION"]
COGNITO_USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
COGNITO_APP_CLIENT_ID = os.environ["COGNITO_APP_CLIENT_ID"]

JWKS_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"

# Cache JWKS keys (persists across warm Lambda invocations)
_jwks_cache = None

async def validate_token(token: str) -> str:
    """Validate Cognito JWT and return user_id (sub claim)."""
    global _jwks_cache
    if _jwks_cache is None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(JWKS_URL)
            _jwks_cache = resp.json()

    claims = jwt.decode(
        token,
        _jwks_cache,
        algorithms=["RS256"],
        audience=COGNITO_APP_CLIENT_ID,
        issuer=f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}",
    )
    return claims["sub"]  # Cognito user ID
```

**JWKS caching**: The keys are cached in the Lambda's memory across warm invocations, so the JWKS endpoint is only hit on cold starts.

---

## Environment Variables

| Variable | Value |
|----------|-------|
| `FLOW_API_URL` | `https://api.yertle.com` (existing Flow API) |
| `COGNITO_REGION` | `us-east-1` |
| `COGNITO_USER_POOL_ID` | (from existing Cognito stack) |
| `COGNITO_APP_CLIENT_ID` | (new app client for MCP) |

---

## Deployment

Deployed via CloudFormation, matching the existing backend pipeline.

### New CloudFormation Resources

```yaml
# Simplified — actual template will follow existing patterns in deployment/infrastructure/

FlowMCPLambda:
  Type: AWS::Lambda::Function
  Properties:
    FunctionName: flow-mcp-server
    Runtime: python3.11
    Handler: lambda_handler.handler
    MemorySize: 256
    Timeout: 30
    Environment:
      Variables:
        FLOW_API_URL: !GetAtt FlowAPIGateway.Endpoint
        COGNITO_REGION: us-east-1
        COGNITO_USER_POOL_ID: !Ref CognitoUserPool
        COGNITO_APP_CLIENT_ID: !Ref MCPAppClient

FlowMCPHttpApi:
  Type: AWS::ApiGatewayV2::Api
  Properties:
    Name: flow-mcp-api
    ProtocolType: HTTP

MCPAppClient:
  Type: AWS::Cognito::UserPoolClient
  Properties:
    UserPoolId: !Ref CognitoUserPool
    ClientName: flow-mcp-server
    AllowedOAuthFlows: [code]
    AllowedOAuthScopes: [openid, profile]
    CallbackURLs: ["https://claude.ai/api/mcp/auth_callback"]
    SupportedIdentityProviders: [COGNITO]
```

### Deployment Steps

1. Add MCP Lambda + API Gateway to the existing CloudFormation pipeline
2. Create the new Cognito App Client
3. Configure custom domain `mcp.yertle.com` → API Gateway
4. Deploy via `make deploy` (or a new `make deploy-mcp` target)

---

## Client Configuration (Production)

### Claude.ai Web UI

1. Go to **Settings > Connectors > Add custom connector**
2. Enter: `https://mcp.yertle.com/mcp`
3. Cognito login page opens in the browser
4. Sign in with Flow credentials, approve permissions
5. Done — Claude.ai interacts with Flow on your behalf

### Claude Desktop (remote)

```json
{
  "mcpServers": {
    "flow": {
      "type": "http",
      "url": "https://mcp.yertle.com/mcp"
    }
  }
}
```

OAuth login triggers in a browser window on first use.

---

## Cost Estimate

| Component | Cost |
|-----------|------|
| **Lambda** | ~$0/month at low volume (1M free requests/month) |
| **API Gateway HTTP API** | $1/million requests |
| **Cognito** | Free for first 50k MAU |
| **Route 53** | $0.50/month per hosted zone |
| **ACM certificate** | Free |

**Total for low-medium usage: effectively $0-1/month**

---

## Cold Start Mitigation

Lambda cold starts (typically 1-3 seconds for Python) may affect the first MCP request. Options if this becomes an issue:

- **Provisioned concurrency**: Keep 1 warm instance ($0.015/hour ≈ $11/month)
- **Lambda SnapStart**: Not yet available for Python (Java/Node only as of early 2026)
- **Ping schedule**: CloudWatch Events rule to invoke the Lambda every 5 minutes to keep it warm (free)

For MVP, cold starts are acceptable — the user only experiences a slight delay on the first tool call after inactivity.

---

## Files to Add for Production

```
mcp/
├── server.py              # (same as local, no changes needed)
├── flow_client.py         # (same as local, no changes needed)
├── config.py              # Add production config reading (Cognito env vars)
├── auth.py                # NEW: OAuth metadata endpoints + Cognito JWT validation
├── lambda_handler.py      # NEW: Mangum wrapper for Lambda
└── requirements.txt       # Add: python-jose[cryptography], mangum

deployment/
├── infrastructure/
│   └── mcp-stack.yaml     # NEW: CloudFormation for Lambda + API Gateway + Cognito App Client
└── hooks/
    └── mcp/
        └── build-mcp.sh   # NEW: Package Lambda zip
```
