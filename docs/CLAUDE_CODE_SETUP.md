# Claude Code Setup — Remote MCP Server

## Current Setup Command

```bash
claude mcp add --transport http --callback-port 9876 yertle-dev https://api-blue-dev.albertcmiller.com/mcp
```

This adds the remote Yertle MCP server to Claude Code. On first use, it opens a browser for Cognito login. After authenticating, tools like `list_organizations`, `create_node`, and `push_node_state` are available in the conversation.

To remove:
```bash
claude mcp remove yertle-dev
```

## Why Each Flag Is Needed

### `--transport http`

By default, `claude mcp add` assumes a **stdio** transport (local subprocess). Since our MCP server is a remote HTTPS endpoint (not a local process), we must specify `http` transport explicitly.

**Could this go away?** Possibly. If Claude Code auto-detects transport based on whether the argument is a URL vs. a command, the flag becomes unnecessary. This is a Claude Code UX decision, not something we control.

### `--callback-port 9876`

Claude Code's OAuth flow starts a temporary localhost HTTP server to receive the auth callback. By default, it picks a **random available port** (e.g., `http://localhost:52446/callback`). 

The problem: AWS Cognito requires callback URLs to be **pre-registered exactly**, including the port. Cognito doesn't support wildcard ports. Our Cognito OAuth client has `http://localhost:9876/callback` registered, so we pin Claude Code to port 9876 to match.

Without this flag, Cognito returns `redirect_mismatch` and authentication fails.

**Could this go away?** Yes, via several paths (see Future Solutions below).

## Ideal Future Command

```bash
claude mcp add yertle https://mcp.yertle.com
```

No flags, short URL. This requires:
1. A custom domain (`mcp.yertle.com`) instead of the dev API Gateway URL
2. Eliminating the `--callback-port` requirement
3. Claude Code auto-detecting HTTP transport from a URL

## Future Solutions for `--callback-port`

### Option 1: Claude Code fixes RFC 8252 compliance (best — no work for us)

[Claude Code issue #42765](https://github.com/anthropics/claude-code/issues/42765) tracks that Claude Code uses `http://localhost:PORT/callback` but RFC 8252 Section 7.3 says native apps MUST use `http://127.0.0.1:PORT/callback` (loopback IP, not hostname). Per the RFC, authorization servers SHOULD allow any port on loopback IPs.

If Cognito follows the RFC for `127.0.0.1` (allowing dynamic ports), and Claude Code switches to `127.0.0.1`, the flag becomes unnecessary. **Status:** open issue, no timeline.

### Option 2: Client ID Metadata Documents (CIMD)

The November 2025 MCP spec revision introduced CIMD as the preferred registration method. With CIMD, Claude Code identifies itself via a metadata URL (e.g., `https://claude.ai/.well-known/mcp-client.json`) instead of dynamic DCR. The server pre-registers this known client with the correct callback URLs.

If Claude Code adopts CIMD, our server can map it to a pre-configured Cognito client that already has the right callback URLs. No `--callback-port` needed.

**Status:** CIMD is in the spec but Claude Code support is unclear.

### Option 3: OAuth callback proxy (we build it)

Add an `/oauth/callback` route on our MCP Lambda as a fixed callback URL. When Cognito redirects there, our Lambda forwards the auth code to Claude Code's localhost.

```
Cognito → https://api.yertle.com/oauth/callback?code=...&state=...
  → MCP Lambda reads state, looks up the original localhost redirect
  → Redirects to http://localhost:52446/callback?code=...&state=...
```

This eliminates the `--callback-port` requirement entirely but adds complexity (state management, security considerations). The [FastMCP OAuth Proxy](https://gofastmcp.com/servers/auth/oauth-proxy) implements this pattern.

**Status:** not implemented. Consider if Options 1/2 don't materialize.

### Option 4: Dynamic Cognito client update (quick fix)

Have our DCR handler call `UpdateUserPoolClient` to append the requested `redirect_uri` to the Cognito client's callback URLs. This way any localhost port works automatically.

Downsides: callback URLs accumulate (100 limit), security surface (anyone can add URLs), requires `cognito-idp:UpdateUserPoolClient` IAM permission.

**Status:** not implemented. Pragmatic but not clean.

## Platform Comparison

| Platform | Setup | Callback Port Needed? |
|----------|-------|----------------------|
| **claude.ai** | Settings > Connectors > Add custom connector | No — uses `https://claude.ai/api/mcp/auth_callback` |
| **Claude Desktop** | Edit `claude_desktop_config.json` | TBD — needs testing |
| **Claude Code** | `claude mcp add --transport http --callback-port 9876 ...` | Yes — random port vs. Cognito exact match |

## References

- [Claude Code MCP docs](https://code.claude.com/docs/en/mcp)
- [Claude Code OAuth callback issue #42765](https://github.com/anthropics/claude-code/issues/42765)
- [Claude Code OAuth redirect_uri issue #10439](https://github.com/anthropics/claude-code/issues/10439)
- [FastMCP OAuth Proxy](https://gofastmcp.com/servers/auth/oauth-proxy)
- [MCP Authorization Spec](https://modelcontextprotocol.io/specification/2025-03-26/basic/authorization)
