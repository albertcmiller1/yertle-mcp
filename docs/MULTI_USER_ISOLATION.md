# Multi-User Isolation for MCP Server

> **Status:** Planned — not yet implemented. This document outlines the path from the current shared-client model to full per-user isolation.

## Current State

All MCP clients (claude.ai, Claude Desktop, Claude Code) share a single Cognito app client (`OAuthUserPoolClient`). When a user connects via OAuth:

1. DCR returns the **same `client_id`** to every registrant
2. User authenticates via Cognito hosted UI (their own identity)
3. Cognito issues an access token with `client_id = OAuthUserPoolClient`
4. MCP Lambda passes this token through to the Flow API
5. Flow API accepts it (audience matches)

**What works:** Each user authenticates as themselves. Their `sub` (Cognito user ID) is in the token. The Flow API knows which user is making the request — org memberships, permissions, and data access are per-user.

**What doesn't:** All MCP clients use the same `client_id`. This means:
- **Audit logs** can distinguish users (by `sub`) but not clients (all show the same `client_id`)
- **Revoking access** for one MCP client (e.g., Claude Desktop) would revoke access for all MCP clients
- **Rate limiting** per client is not possible — only per user

## When This Matters

For a small team, the current model is fine. It becomes a problem when:
- You need to audit which AI tool made a specific change
- You want to revoke access for a specific integration (e.g., revoke Claude Desktop but keep claude.ai)
- Compliance requires per-client tracking
- You want per-client rate limits or scopes

## Proposed Solution: Client ID Metadata Documents (CIMD)

The November 2025 MCP spec revision introduced **Client ID Metadata Documents (CIMD)** as the preferred registration method, replacing per-instance DCR. CIMD groups all instances of the same client under one identity.

### How CIMD Works

Instead of each Claude Desktop instance registering independently, all Claude Desktop instances share a single metadata URL:

```
https://claude.ai/.well-known/mcp-client.json
```

This URL returns a JSON document describing the client:

```json
{
  "client_id": "https://claude.ai/.well-known/mcp-client.json",
  "client_name": "Claude Desktop",
  "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
  "grant_types": ["authorization_code"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}
```

The MCP server validates the metadata document and maps it to a Cognito app client. Different MCP clients (Claude Desktop, claude.ai, Claude Code) have different metadata URLs, so the server can distinguish them.

### Implementation Plan

#### Phase 1: Server-Side CIMD Support

Add CIMD handling to the MCP Lambda's `/register` endpoint:

```python
async def _handle_client_registration(self, scope, receive, send):
    request_data = json.loads(body)
    
    # Check if client_id is a URL (CIMD) or absent (legacy DCR)
    client_id = request_data.get("client_id")
    
    if client_id and client_id.startswith("https://"):
        # CIMD: fetch and validate the metadata document
        metadata = await fetch_client_metadata(client_id)
        validate_redirect_uris(metadata, request_data)
        
        # Map to a per-client Cognito app client
        cognito_client = get_or_create_cognito_client(
            client_name=metadata["client_name"],
            callback_urls=metadata["redirect_uris"],
        )
        return cognito_client["ClientId"]
    else:
        # Legacy DCR: return pre-registered client
        return os.environ["COGNITO_APP_CLIENT_ID"]
```

#### Phase 2: Per-Client Cognito App Clients

Create a small set of Cognito app clients for known MCP client types:

| Client | Cognito App Client | Callback URLs |
|--------|-------------------|---------------|
| claude.ai | `yertle-mcp-claude-web` | `https://claude.ai/api/mcp/auth_callback` |
| Claude Desktop | `yertle-mcp-claude-desktop` | (Claude Desktop's callback) |
| Claude Code | `yertle-mcp-claude-code` | (Claude Code's callback) |
| yertle-cli | `yertle-mcp-cli` | `http://localhost:9876/callback` |

These are pre-registered in CloudFormation (not created dynamically). The CIMD handler maps the metadata URL to the corresponding client.

#### Phase 3: Audience-Aware API Gateway

Add all per-client `client_id` values to the Flow API Gateway's `CognitoAuthorizer` audience list:

```yaml
CognitoAuthorizer:
  JwtConfiguration:
    Audience:
      - !Ref CognitoUserPoolClientId      # frontend
      - !Ref OAuthUserPoolClientId         # shared MCP (legacy)
      - !Ref ClaudeWebClientId             # claude.ai
      - !Ref ClaudeDesktopClientId         # Claude Desktop
      - !Ref ClaudeCodeClientId            # Claude Code
      - !Ref CLIClientId                   # yertle-cli
```

#### Phase 4: Audit and Rate Limiting

With per-client tokens, you can:
- **Audit:** CloudWatch logs show which `client_id` made each request
- **Revoke:** Delete a specific Cognito app client to revoke one integration
- **Rate limit:** API Gateway usage plans per client

## Migration Path

1. **Phase 1 is backward-compatible.** The `/register` endpoint continues to return the shared `OAuthUserPoolClient` for legacy DCR requests. CIMD clients get per-client IDs.
2. **No client-side changes needed.** Claude clients already support CIMD (since Nov 2025 MCP spec). The server just needs to handle the metadata URL in the registration request.
3. **Gradual rollout.** Start by supporting CIMD for claude.ai (the most common client), then add Claude Desktop and Claude Code as their metadata URLs become known.

## Files to Modify

| File | Change |
|------|--------|
| `yertle-mcp/app.py` | Update `_handle_client_registration()` to detect CIMD and map to per-client Cognito clients |
| `yertle/deployment/infrastructure/shared/cognito.yml` | Add per-client Cognito app clients |
| `yertle/deployment/infrastructure/backend/api-core.yml` | Add per-client audiences to CognitoAuthorizer |
| `yertle/deployment/dev.yaml` | Add per-client config and dynamic params |

## References

- [Evolving OAuth Client Registration in MCP](https://blog.modelcontextprotocol.io/posts/client_registration/)
- [Client ID Metadata Documents spec](https://www.scalekit.com/blog/what-is-cimd)
- [MCP Authorization Spec (2025-11-25)](https://modelcontextprotocol.io/specification/2025-03-26/basic/authorization)
