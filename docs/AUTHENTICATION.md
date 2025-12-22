# Session Token Authentication for diagnostic-mcp

This document describes the session token-based authentication system for diagnostic-mcp HTTP server.

## Overview

diagnostic-mcp supports optional session token authentication for production deployments. When enabled, all endpoints except `/health*` and `/info` require a valid Bearer token in the `Authorization` header.

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AUTH_ENABLED` | No | `false` | Enable authentication |
| `AUTH_ADMIN_TOKEN` | Yes (if enabled) | - | Admin token for bootstrapping |
| `AUTH_TOKEN_TTL` | No | `24` | Token TTL in hours |
| `AUTH_STORAGE` | No | `memory` | Storage backend: `memory` or `supabase` |
| `SUPABASE_URL` | Yes (if supabase) | - | Supabase project URL |
| `SUPABASE_KEY` | Yes (if supabase) | - | Supabase service role key |

### Example Configuration

#### In-Memory Storage (Development)

```bash
# .env
AUTH_ENABLED=true
AUTH_ADMIN_TOKEN=$(uuidgen)  # Generate secure random token
AUTH_TOKEN_TTL=24
AUTH_STORAGE=memory
```

#### Supabase Storage (Production)

```bash
# .env
AUTH_ENABLED=true
AUTH_ADMIN_TOKEN=$(uuidgen)
AUTH_TOKEN_TTL=168  # 1 week
AUTH_STORAGE=supabase
SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_KEY=your-service-role-key
```

## Database Setup (Supabase Storage)

If using Supabase storage, run the migration to create the `auth_tokens` table:

```bash
# Run migration
psql $DATABASE_URL -f migrations/002_auth_tokens.sql
```

Or apply via Supabase dashboard:
1. Go to SQL Editor
2. Copy contents of `migrations/002_auth_tokens.sql`
3. Execute

## Usage

### 1. Bootstrap with Admin Token

The admin token is used to create the first session token. Store it securely and never expose it.

```bash
# Generate a secure admin token
export AUTH_ADMIN_TOKEN=$(uuidgen)

# Start server with auth enabled
AUTH_ENABLED=true AUTH_ADMIN_TOKEN=$AUTH_ADMIN_TOKEN python http_server.py
```

### 2. Create Session Token

Use the admin token to create a session token via POST `/auth/token`:

```bash
# Create token with default TTL (24h)
curl -X POST http://localhost:5555/auth/token \
  -H "Authorization: Bearer $AUTH_ADMIN_TOKEN" \
  -H "Content-Type: application/json"

# Create token with custom TTL (72h)
curl -X POST http://localhost:5555/auth/token \
  -H "Authorization: Bearer $AUTH_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ttl_hours": 72, "metadata": {"purpose": "CI/CD pipeline"}}'
```

Response:
```json
{
  "status": "success",
  "message": "Token created successfully",
  "data": {
    "token": "550e8400-e29b-41d4-a716-446655440000",
    "token_id": "660e8400-e29b-41d4-a716-446655440001",
    "expires_at": "2025-12-22T19:00:00.000Z",
    "ttl_hours": 24.0
  },
  "timestamp": "2025-12-21T19:00:00.000Z"
}
```

**Important:** Save the `token` value - it's only shown once!

### 3. Use Session Token

Include the session token in the `Authorization` header for all protected requests:

```bash
# Example: Call diagnostic tool
curl -X POST http://localhost:5555/tool/check_all_health \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{"timeout": 10}'

# Example: SSE connection
curl -N http://localhost:5555/sse \
  -H "Authorization: Bearer 550e8400-e29b-41d4-a716-446655440000"
```

### 4. Token Management via MCP Tools

If using diagnostic-mcp via MCP protocol, use the built-in tools:

#### Create Token
```json
{
  "tool": "create_auth_token",
  "arguments": {
    "ttl_hours": 48,
    "metadata": {
      "purpose": "monitoring",
      "client": "prometheus"
    }
  }
}
```

#### Revoke Token
```json
{
  "tool": "revoke_auth_token",
  "arguments": {
    "token_id": "660e8400-e29b-41d4-a716-446655440001"
  }
}
```

#### List Active Tokens
```json
{
  "tool": "list_active_tokens",
  "arguments": {}
}
```

## Endpoint Protection

### Protected Endpoints (Require Authentication)

- `GET /sse` - SSE connection
- `POST /messages/` - MCP message endpoint
- `GET /diagnostics` - Full diagnostic
- `POST /tool/{tool_name}` - Tool execution

### Exempt Endpoints (Public)

- `GET /health*` - All health check endpoints
- `GET /info` - Server info
- `POST /auth/token` - Token creation (requires admin token)

## Security Considerations

### Token Storage

- **Plaintext tokens** are never stored - only SHA256 hashes
- **Admin token** is hashed on initialization
- **Session tokens** are stored as hashes in the database/memory

### Rate Limiting

Token creation is rate-limited:
- **5 tokens per client per 60 seconds**
- Prevents token creation abuse
- Returns HTTP 429 when rate limit exceeded

### Token Validation

- **Constant-time comparison** prevents timing attacks
- **Expiration checking** happens on every request
- **Automatic cleanup** of expired tokens (via periodic task or manual cleanup)

### Best Practices

1. **Use strong admin tokens:**
   ```bash
   # Good: UUID v4 (122 bits entropy)
   AUTH_ADMIN_TOKEN=$(uuidgen)

   # Good: OpenSSL random (256 bits)
   AUTH_ADMIN_TOKEN=$(openssl rand -hex 32)
   ```

2. **Rotate admin token regularly:**
   - Update `AUTH_ADMIN_TOKEN` environment variable
   - Restart server
   - Revoke old tokens and create new ones

3. **Use Supabase storage for production:**
   - Persistent across restarts
   - Centralized token management
   - Database backups included

4. **Set appropriate TTL:**
   - Short TTL (24h) for sensitive operations
   - Longer TTL (168h = 1 week) for CI/CD
   - Very long TTL (8760h = 1 year) for monitoring systems

5. **Monitor token usage:**
   - Log authentication attempts
   - Track failed authentication
   - Alert on suspicious patterns

## Error Responses

### Missing Token
```bash
curl http://localhost:5555/diagnostics

# Response: 401 Unauthorized
{
  "status": "error",
  "error": "Missing Authorization header",
  "message": "Bearer token required",
  "timestamp": "2025-12-21T19:00:00.000Z"
}
```

### Invalid Token
```bash
curl http://localhost:5555/diagnostics \
  -H "Authorization: Bearer invalid-token"

# Response: 401 Unauthorized
{
  "status": "error",
  "error": "Invalid or expired token",
  "message": "Authentication failed",
  "timestamp": "2025-12-21T19:00:00.000Z"
}
```

### Rate Limit Exceeded
```bash
# After 5 token creation requests in 60 seconds
curl -X POST http://localhost:5555/auth/token \
  -H "Authorization: Bearer $AUTH_ADMIN_TOKEN"

# Response: 429 Too Many Requests
{
  "status": "error",
  "error": "Rate limit exceeded",
  "message": "Too many token creation requests",
  "timestamp": "2025-12-21T19:00:00.000Z"
}
```

## Token Lifecycle

```
1. Admin Token Bootstrap
   ↓
2. Create Session Token (POST /auth/token with admin token)
   ↓
3. Use Session Token (Authorization: Bearer <token>)
   ↓
4. Token Expires OR Revoked
   ↓
5. Create New Token (repeat step 2)
```

## Migration from Non-Authenticated Setup

If you have an existing deployment without authentication:

1. **Add auth configuration:**
   ```bash
   # .env
   AUTH_ENABLED=true
   AUTH_ADMIN_TOKEN=$(uuidgen)
   AUTH_STORAGE=memory  # or supabase
   ```

2. **Restart server:**
   ```bash
   python http_server.py
   ```

3. **Create session tokens:**
   ```bash
   # Create token for each client
   curl -X POST http://localhost:5555/auth/token \
     -H "Authorization: Bearer $AUTH_ADMIN_TOKEN"
   ```

4. **Update clients:**
   - Add `Authorization: Bearer <token>` header to all requests
   - Store tokens securely (environment variables, secrets managers)

5. **Monitor logs:**
   - Watch for 401 errors
   - Verify all clients are authenticated

## Troubleshooting

### Authentication Not Working

**Check 1:** Is authentication enabled?
```bash
# Server logs should show:
# "Authentication enabled (TTL: 24h, Storage: memory)"

# If not enabled:
export AUTH_ENABLED=true
```

**Check 2:** Is admin token set?
```bash
# Check environment
echo $AUTH_ADMIN_TOKEN

# If empty:
export AUTH_ADMIN_TOKEN=$(uuidgen)
```

**Check 3:** Is token format correct?
```bash
# Must be: Authorization: Bearer <token>
curl -v http://localhost:5555/diagnostics \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Tokens Not Persisting

**Problem:** Tokens lost after server restart

**Solution:** Use Supabase storage
```bash
# .env
AUTH_STORAGE=supabase
SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_KEY=your-service-role-key
```

### Rate Limit Issues

**Problem:** Cannot create tokens due to rate limit

**Wait:** Rate limit window is 60 seconds
```bash
# Wait 60 seconds, then retry
sleep 60
curl -X POST http://localhost:5555/auth/token ...
```

**Alternative:** Restart server (resets rate limiter for memory storage)

## API Reference

### POST /auth/token

Create a new session token.

**Headers:**
- `Authorization: Bearer <admin_token>` (required)
- `Content-Type: application/json` (optional)

**Body (optional):**
```json
{
  "ttl_hours": 24,
  "metadata": {
    "purpose": "description",
    "client": "client_name"
  }
}
```

**Response: 201 Created**
```json
{
  "status": "success",
  "message": "Token created successfully",
  "data": {
    "token": "550e8400-e29b-41d4-a716-446655440000",
    "token_id": "660e8400-e29b-41d4-a716-446655440001",
    "expires_at": "2025-12-22T19:00:00.000Z",
    "ttl_hours": 24.0
  },
  "timestamp": "2025-12-21T19:00:00.000Z"
}
```

**Errors:**
- `401` - Invalid admin token
- `429` - Rate limit exceeded
- `503` - Authentication not enabled

## Testing

### Test Authentication Setup

```bash
# 1. Start server with auth
AUTH_ENABLED=true AUTH_ADMIN_TOKEN=test-admin python http_server.py

# 2. Verify health endpoint is public
curl http://localhost:5555/health
# Should return 200 OK

# 3. Verify protected endpoint requires auth
curl http://localhost:5555/diagnostics
# Should return 401 Unauthorized

# 4. Create session token
TOKEN=$(curl -s -X POST http://localhost:5555/auth/token \
  -H "Authorization: Bearer test-admin" | jq -r '.data.token')

# 5. Verify session token works
curl http://localhost:5555/diagnostics \
  -H "Authorization: Bearer $TOKEN"
# Should return 200 OK with diagnostic data
```

### Test MCP Tools

```python
# test_auth_tools.py
import asyncio
from diagnostic_mcp.server import handle_create_auth_token

# Mock auth manager must be set first
from diagnostic_mcp import server as diagnostic_server
from diagnostic_mcp.auth import AuthManager, MemoryTokenStorage

auth_manager = AuthManager(
    storage=MemoryTokenStorage(),
    admin_token="test-admin",
    default_ttl_hours=24
)

diagnostic_server.set_auth_manager(auth_manager)

# Test token creation
async def test():
    result = await handle_create_auth_token({
        "ttl_hours": 48,
        "metadata": {"test": "data"}
    })
    print(result)

asyncio.run(test())
```

## Version History

- **v2.1.0** (2025-12-21): Initial authentication implementation
  - Session token authentication
  - Admin token bootstrapping
  - Memory and Supabase storage backends
  - Rate limiting
  - MCP tools for token management
