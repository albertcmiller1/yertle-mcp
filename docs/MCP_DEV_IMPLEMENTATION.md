# MCP Server — Dev/Prod Deployment Implementation

> This document captures the implementation plan for deploying the yertle MCP server to our AWS dev and prod environments. It supersedes the outdated parts of `MCP_DEPLOYMENT.md` — that doc remains as historical reference.

## Overview

The MCP server currently runs locally via stdio with hardcoded email/password credentials. We need to deploy it as a remote MCP server accessible over HTTPS, with proper OAuth 2.1 authentication, integrated into our existing deployment pipeline.

## Key Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Auth method | OAuth 2.1 + PKCE (authorization code flow) | MCP spec standard; natively supported by Claude Desktop and claude.ai |
| OAuth client naming | Generic `OAuth*` (not `MCP*`) | Same Cognito OAuth client will be reused by yertle-cli browser login (future) |
| Cognito domain | Custom domain (`auth.albertcmiller.com` / `auth.yertle.com`) | Professional, branded login experience |
| API Gateway | Add routes to existing backend API Gateway | Reuses existing infrastructure; avoids managing a separate gateway |
| Deployment | Bundle into existing `yertle/deployment/` pipeline | Pipeline is battle-tested; cross-stack dependencies are already wired |
| User identity | Pass through user's OAuth token to Flow API | Each MCP user acts as themselves, not a shared service account |

## What Changed vs. MCP_DEPLOYMENT.md

| Original proposal | Current plan |
|-------------------|--------------|
| Separate API Gateway + `mcp.yertle.com` domain | Routes on existing API Gateway (`api-{slot}-{env}.{domain}`) |
| Standalone CloudFormation stack | Integrated into existing pipeline steps |
| MCP server authenticates with its own credentials | User's OAuth token passed through to Flow API |
| Cognito prefix domain for hosted UI | Custom domain (`auth.{domain}`) |

## Architecture

```
Claude Desktop / claude.ai
        │
        │  HTTPS (POST /mcp, GET /.well-known/*)
        ▼
┌─────────────────────────────────────────────────┐
│  API Gateway (existing: flow-api-{env})         │
│                                                 │
│  Existing routes ──► Backend Lambda             │
│    GET /health (no auth)                        │
│    POST /auth/signin (no auth)                  │
│    POST /auth/refresh (no auth)                 │
│    ANY /{proxy+} (Cognito JWT authorizer)       │
│                                                 │
│  New MCP routes ──► MCP Lambda (NEW)            │
│    POST /mcp (OAuth JWT authorizer)              │
│    GET /mcp (OAuth JWT authorizer)               │
│    GET /.well-known/oauth-protected-resource    │
│         (no auth)                               │
└─────────────────────────────────────────────────┘
        │                            │
        ▼                            ▼
  Backend Lambda              MCP Lambda (NEW)
  (existing)                  ┌──────────────┐
                              │ Mangum       │
                              │ FastMCP      │
                              │ auth.py      │
                              └──────┬───────┘
                                     │ HTTP (user's Bearer token)
                                     ▼
                              Backend API (same gateway)
```

## OAuth 2.1 Flow

1. Claude client sends unauthenticated `POST /mcp` to API Gateway
2. API Gateway returns `401` (JWT authorizer rejects — no token)
3. Claude fetches `GET /.well-known/oauth-protected-resource` from same gateway
4. Response identifies Cognito as authorization server:
   ```json
   {
     "resource": "https://api-blue-dev.albertcmiller.com/mcp",
     "authorization_servers": ["https://auth.albertcmiller.com"]
   }
   ```
5. Claude discovers Cognito's OAuth endpoints via `/.well-known/openid-configuration`
6. Claude opens browser → user logs in via Cognito hosted UI
7. Cognito redirects to `https://claude.ai/api/mcp/auth_callback` with auth code
8. Claude exchanges code for tokens (PKCE S256 verification)
9. Subsequent `POST /mcp` requests include `Authorization: Bearer <access_token>`
10. API Gateway validates JWT via OAuth authorizer → routes to MCP Lambda

## Implementation Steps

### Step 1: Cognito OAuth Resources

**File: `yertle/deployment/infrastructure/shared/cognito.yml`**

Add to existing template (no changes to existing resources):

```yaml
# Cognito custom domain for hosted UI (OAuth endpoints)
UserPoolDomain:
  Type: AWS::Cognito::UserPoolDomain
  Properties:
    Domain: !Sub 'auth.${Domain}'
    UserPoolId: !Ref UserPool
    CustomDomainConfig:
      CertificateArn: !Ref AcmCertificateArn

# Route53 record for Cognito custom domain
UserPoolDomainDNS:
  Type: AWS::Route53::RecordSet
  Properties:
    HostedZoneId: !Ref HostedZoneId
    Name: !Sub 'auth.${Domain}'
    Type: A
    AliasTarget:
      DNSName: !GetAtt UserPoolDomain.CloudFrontDistribution
      HostedZoneId: Z2FDTNDATAQYW2  # CloudFront global hosted zone ID

# Shared OAuth app client (used by MCP server, CLI browser login, and future OAuth clients)
OAuthUserPoolClient:
  Type: AWS::Cognito::UserPoolClient
  Properties:
    UserPoolId: !Ref UserPool
    ClientName: !Sub '${StackName}-oauth-client'
    GenerateSecret: false
    AllowedOAuthFlows:
      - code
    AllowedOAuthFlowsUserPoolClient: true
    AllowedOAuthScopes:
      - openid
      - profile
      - email
    CallbackURLs:
      - 'https://claude.ai/api/mcp/auth_callback'
      - 'http://localhost:9876/callback'  # yertle-cli browser login (future)
    LogoutURLs:
      - 'https://claude.ai'
    SupportedIdentityProviders:
      - COGNITO
    ExplicitAuthFlows:
      - ALLOW_REFRESH_TOKEN_AUTH
    TokenValidityUnits:
      AccessToken: hours
      IdToken: hours
      RefreshToken: days
    AccessTokenValidity: 24
    IdTokenValidity: 24
    RefreshTokenValidity: 30
    PreventUserExistenceErrors: ENABLED
```

New parameters to add: `Domain`, `AcmCertificateArn`, `HostedZoneId`.

New outputs: `OAuthUserPoolClientId`, `UserPoolDomainName`.

**Note:** The ACM certificate for `auth.albertcmiller.com` must be in **us-east-1** (Cognito custom domains use CloudFront under the hood). The existing cert (`a08108a5-...`) covers `albertcmiller.com` — check if it's a wildcard (`*.albertcmiller.com`) or if a new cert is needed for `auth.albertcmiller.com`.

### Step 2: MCP Lambda Function

**File: `yertle/deployment/infrastructure/backend/mcp-lambda.yml`** (new)

CloudFormation template for the MCP Lambda:
- Function name: `{env}-yertle-mcp-{slot}`
- Runtime: Python 3.13
- Memory: 256 MB
- Timeout: 30 seconds
- Handler: `lambda_handler.handler`
- Code: `s3://{env}-yertle-deployments/lambda/{slot}/{version}/mcp.zip`
- Environment variables:
  - `FLOW_API_URL` — `https://api-{slot}-{env}.{domain}` (public URL of backend)
  - `COGNITO_REGION` — `us-east-1`
  - `COGNITO_USER_POOL_ID` — from Cognito stack output
  - `COGNITO_APP_CLIENT_ID` — OAuth client ID from Cognito stack output
- IAM: CloudWatch Logs only (no direct AWS service access needed — MCP proxies to Flow API over HTTP)

### Step 3: API Gateway Routes

**File: `yertle/deployment/infrastructure/backend/api-core.yml`** (modify)

Add to the existing template:

**New parameters:**
- `MCPLambdaArn` — ARN of the MCP Lambda function
- `OAuthUserPoolClientId` — OAuth app client ID (different audience from backend)

**New resources:**

```yaml
# Separate Lambda integration for MCP
MCPLambdaIntegration:
  Type: AWS::ApiGatewayV2::Integration
  Properties:
    ApiId: !Ref HttpApi
    IntegrationType: AWS_PROXY
    IntegrationUri: !Sub 'arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${MCPLambdaArn}/invocations'
    PayloadFormatVersion: '2.0'

# JWT authorizer for OAuth clients (MCP server, CLI browser login, future OAuth clients)
# Separate from the existing CognitoAuthorizer which validates against the frontend app client
OAuthCognitoAuthorizer:
  Type: AWS::ApiGatewayV2::Authorizer
  Properties:
    ApiId: !Ref HttpApi
    AuthorizerType: JWT
    Name: OAuthCognitoJWTAuthorizer
    IdentitySource:
      - $request.header.Authorization
    JwtConfiguration:
      Issuer: !Sub
        - 'https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPoolId}'
        - UserPoolId: !Select [1, !Split ['/', !Select [5, !Split [':', !Ref CognitoUserPoolArn]]]]
      Audience:
        - !Ref OAuthUserPoolClientId

# MCP route (authenticated via OAuth authorizer)
MCPRoute:
  Type: AWS::ApiGatewayV2::Route
  Properties:
    ApiId: !Ref HttpApi
    RouteKey: 'POST /mcp'
    AuthorizationType: JWT
    AuthorizerId: !Ref OAuthCognitoAuthorizer
    Target: !Sub 'integrations/${MCPLambdaIntegration}'

# MCP SSE route (if needed)
MCPGetRoute:
  Type: AWS::ApiGatewayV2::Route
  Properties:
    ApiId: !Ref HttpApi
    RouteKey: 'GET /mcp'
    AuthorizationType: JWT
    AuthorizerId: !Ref OAuthCognitoAuthorizer
    Target: !Sub 'integrations/${MCPLambdaIntegration}'

# OAuth metadata discovery (no auth)
OAuthMetadataRoute:
  Type: AWS::ApiGatewayV2::Route
  Properties:
    ApiId: !Ref HttpApi
    RouteKey: 'GET /.well-known/oauth-protected-resource'
    AuthorizationType: NONE
    Target: !Sub 'integrations/${MCPLambdaIntegration}'

# Lambda permission for MCP function
MCPLambdaApiPermission:
  Type: AWS::Lambda::Permission
  Properties:
    Action: lambda:InvokeFunction
    FunctionName: !Ref MCPLambdaArn
    Principal: apigateway.amazonaws.com
    SourceArn: !Sub 'arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*/*'
```

**Important:** The `GET /.well-known/oauth-protected-resource` route must be defined as a specific route (not caught by `ANY /{proxy+}`) so it reaches the MCP Lambda without auth. API Gateway v2 matches more-specific routes first, so this should work.

### Step 4: MCP Server Code Changes

#### `yertle-mcp/lambda_handler.py` (new)

Mangum wrapper to run FastMCP's ASGI app inside Lambda:
```python
from mangum import Mangum
from app import server
import tools
import resources

handler = Mangum(server.asgi_app())
```

#### `yertle-mcp/auth.py` (new)

- Cognito JWT validation (RS256 via JWKS)
- Cache JWKS keys in module-level variable (survives across warm Lambda invocations)
- Extract `sub` (user ID) and `email` from validated token
- Serve `/.well-known/oauth-protected-resource` response

#### `yertle-mcp/config.py` (modify)

- Detect Lambda environment via `AWS_LAMBDA_FUNCTION_NAME` env var
- In Lambda mode: read `FLOW_API_URL`, `COGNITO_*` from env vars
- In local mode: keep existing behavior (email/password from env vars)

#### `yertle-mcp/flow_client.py` (modify)

Key change: in production, the MCP server should **not** authenticate with its own credentials. Instead:
- Accept the user's Bearer token from the incoming MCP request
- Pass it through as `Authorization: Bearer <token>` when calling the Flow API
- Remove the `_sign_in()` / `_refresh()` logic for production mode (the user's token is already validated by API Gateway)

This means each MCP user operates as themselves — their org memberships, permissions, and audit trail are preserved.

#### `yertle-mcp/requirements.txt` (new)

```
mcp
httpx
mangum
python-jose[cryptography]
```

### Step 5: Build Hook

**File: `yertle/deployment/hooks/backend/build-mcp-package.sh`** (new)

Follow the same pattern as `build-versioned-package.sh`:
1. Get version from `$VERSION` or `git describe`
2. Create build dir, pip install from `requirements.txt`
3. Copy `yertle-mcp/` source files (not the `docs/` directory)
4. Zip and upload to `s3://{bucket}/lambda/{slot}/{version}/mcp.zip`

**Note:** The build script needs access to the `yertle-mcp/` directory. Since the deployment pipeline runs from the `yertle/` repo root, the script will need to reference `../yertle-mcp/` or accept the MCP source path as a parameter.

### Step 6: Pipeline Integration

**File: `yertle/deployment/dev.yaml`** (modify)

Add to backend pipeline:
```yaml
pipelines:
  deploy:
    backend:
      - id: deploy-backend
        description: Deploy backend lambdas + API Gateway
        steps:
          - hooks.check_alembic_version
          - hooks.build_lambda_package
          - hooks.build_mcp_package          # NEW
          - cloudformations.backend.lambda
          - cloudformations.backend.mcp_lambda  # NEW
          - cloudformations.backend.apigateway   # Updated (has new MCP params)
```

Add CloudFormation config:
```yaml
cloudformations:
  backend:
    mcp_lambda:
      template: deployment/infrastructure/backend/mcp-lambda.yml
      stack_name: "{env}-yertle-mcp-{slot}"
      description: CFT for MCP Lambda
      parameters:
        Environment: *env
        Slot: *slot
        StackName: "{env}-yertle-mcp-{slot}"
        Version: "{version}"
        FlowApiUrl: "https://api-{slot}-{env}.{domain}"
      dynamic_parameters:
        CognitoUserPoolId:
          command: "aws cloudformation describe-stacks --stack-name {env}-yertle-backend-cognito --region {region} --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text"
        OAuthClientId:
          command: "aws cloudformation describe-stacks --stack-name {env}-yertle-backend-cognito --region {region} --query 'Stacks[0].Outputs[?OutputKey==`OAuthUserPoolClientId`].OutputValue' --output text"
```

Add hook config:
```yaml
hooks:
  build_mcp_package:
    script: deployment/hooks/backend/build-mcp-package.sh
    description: Build MCP Lambda Package
    parameters:
      environment: *env
      deployment_bucket: *shared_s3_bucket_name
      mcp_source_dir: ../yertle-mcp
      slot: *slot
```

Update `apigateway` config to include new dynamic parameters:
```yaml
    apigateway:
      # ... existing config ...
      dynamic_parameters:
        # ... existing params ...
        MCPLambdaArn:
          command: "aws cloudformation describe-stacks --stack-name {env}-yertle-mcp-{slot} --region {region} --query 'Stacks[0].Outputs[?OutputKey==`MCPLambdaArn`].OutputValue' --output text"
        OAuthUserPoolClientId:
          command: "aws cloudformation describe-stacks --stack-name {env}-yertle-backend-cognito --region {region} --query 'Stacks[0].Outputs[?OutputKey==`OAuthUserPoolClientId`].OutputValue' --output text"
```

## Open Questions

1. **ACM Certificate:** Does the existing cert (`a08108a5-...`) cover `auth.albertcmiller.com`? If not, we need a new cert or a wildcard cert. Cognito custom domains require the cert to be in us-east-1.

2. **Cross-repo build:** The build hook runs from `yertle/` but needs `yertle-mcp/` source. Using `../yertle-mcp/` works locally but may not in CI. Alternative: copy MCP source into the yertle repo's build context, or accept the sibling path as a parameter.

3. **Claude Desktop callback URLs:** The current plan includes `https://claude.ai/api/mcp/auth_callback` and `http://localhost:9876/callback` (for CLI). Claude Desktop may use a different callback URL — we may need to add it to the OAuth client once we test.

4. **`.well-known` route priority:** Verify that API Gateway v2 routes `GET /.well-known/oauth-protected-resource` (specific, no auth) before the catch-all `ANY /{proxy+}` (auth required). API Gateway v2 should prefer the more specific route, but this needs testing.

## Verification Plan

1. **Deploy shared stack** — verify Cognito hosted UI at `https://auth.albertcmiller.com`
2. **Deploy backend stack** — verify MCP Lambda + new routes created
3. **Test OAuth discovery:**
   ```bash
   curl https://api-blue-dev.albertcmiller.com/.well-known/oauth-protected-resource
   ```
4. **Test Cognito login:** Open `https://auth.albertcmiller.com/login?client_id=...&response_type=code&scope=openid+profile+email&redirect_uri=...` in browser
5. **Test MCP integration:** Configure Claude Desktop with remote URL, verify OAuth prompt and tool calls
6. **Regression check:** Verify frontend and CLI auth still work (existing Cognito client unchanged)

## Future: CLI Browser-Based Login

The OAuth infrastructure built here (Cognito hosted UI, `OAuthUserPoolClient`) is designed to be reused by `yertle-cli` for browser-based login, replacing the current email/password prompt. The flow:

1. `yertle auth login` opens the user's browser to the Cognito hosted UI
2. CLI starts a temporary localhost HTTP server (`http://localhost:9876/callback`)
3. User logs in via Cognito in the browser
4. Cognito redirects to localhost with auth code
5. CLI exchanges code for tokens (PKCE) and stores in `~/.yertle/config.json`

This is already supported by the `OAuthUserPoolClient` — the localhost callback URL is included. The only work needed is on the `yertle-cli` side (`cmd/auth.go`), no infrastructure changes required.

Benefits: no passwords in terminals, MFA-ready, SSO-ready (if identity providers are added to Cognito later).

## Future: Other Considerations

- **Provisioned concurrency:** If cold starts (1-3s) are noticeable, add provisioned concurrency ($11/month) or a CloudWatch ping schedule
- **Custom scopes:** Add `yertle:read`, `yertle:write` OAuth scopes for fine-grained permissions
- **Deployment pipeline evolution:** Consider moving to a per-repo pipeline or CDK when complexity warrants it
