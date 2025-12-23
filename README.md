# diagnostic-mcp

[![PyPI version](https://badge.fury.io/py/diagnostic-mcp.svg)](https://badge.fury.io/py/diagnostic-mcp)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](https://github.com/latvian-lab/diagnostic-mcp/actions)

MCP server providing comprehensive diagnostic tools for monitoring and validating MCP infrastructure health and configuration.

## Overview

diagnostic-mcp provides comprehensive diagnostic capabilities for monitoring and validating the health and configuration of all MCP servers in your system. It offers health probes, port consistency checks, configuration validation, and comprehensive diagnostic reports.

## Features

### Core Features

- **Session Token Authentication** - Optional Bearer token authentication for production deployments
- **Health Probes** - Kubernetes-style liveness, readiness, and startup probes
- **HTTP/SSE Transport** - Full HTTP wrapper around MCP protocol
- **Diagnostic Tools** - 5 comprehensive diagnostic tools for MCP infrastructure

### 5 Diagnostic Tools

1. **check_port_consistency** - Port assignment validation
   - Detects port conflicts (multiple servers on same port)
   - Finds gaps in port sequence (5555-5582)
   - Identifies servers without port assignments
   - Flags ports outside expected range

2. **check_all_health** - SSE endpoint health monitoring
   - Tests all MCP server SSE endpoints in parallel
   - Measures response times
   - Categorizes servers: online, offline, error
   - Configurable timeout (default: 5s)

3. **check_configurations** - settings.json validation
   - Verifies use of supergateway
   - Checks args format consistency
   - Validates description fields
   - Reports configuration issues per server

4. **check_tool_availability** - Tool inventory (limited)
   - Counts registered MCP servers
   - Notes: Full tool querying requires MCP protocol client

5. **run_full_diagnostic** - Comprehensive report
   - Runs all 4 checks above
   - Aggregates results
   - Provides summary with total/critical issue counts
   - Generates actionable recommendations

## Installation

### From PyPI (Recommended)

```bash
pip install diagnostic-mcp
```

### From Source (Development)

```bash
git clone https://github.com/latvian-lab/diagnostic-mcp.git
cd diagnostic-mcp
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

### Stdio Mode (Claude Code)

```bash
python -m diagnostic_mcp.server
```

### SSE Mode (ContextForge Gateway)

```bash
python sse_server.py
# Default port: 5583
# Custom port: python sse_server.py --port 6583
```

### Health Check

```bash
curl http://localhost:5583/health
```

## Tool Examples

### Check Port Consistency

```python
# No parameters required
mcp__diagnostic-mcp__check_port_consistency()

# Returns:
# {
#   "port_map": {"server-name": 5555, ...},
#   "conflicts": [{"port": 5555, "servers": ["server1", "server2"]}],
#   "gaps": [5560, 5561],
#   "servers_without_ports": [],
#   "summary": {
#     "total_servers": 26,
#     "issues_found": 2
#   }
# }
```

### Check All Health

```python
# Default 5s timeout
mcp__diagnostic-mcp__check_all_health()

# Custom timeout
mcp__diagnostic-mcp__check_all_health(timeout=10)

# Returns:
# {
#   "servers_online": 24,
#   "servers_offline": 2,
#   "servers_error": 0,
#   "online_servers": [
#     {"name": "knowledge-mcp", "port": 5555, "status": "online", "response_time_ms": 45.2}
#   ],
#   "offline_servers": [...]
# }
```

### Check Configurations

```python
# No parameters required
mcp__diagnostic-mcp__check_configurations()

# Returns:
# {
#   "total_servers": 26,
#   "consistent_format": 24,
#   "servers_with_issues": 2,
#   "issues": [
#     {
#       "server": "old-mcp",
#       "issues": ["not using supergateway", "missing description"]
#     }
#   ]
# }
```

### Run Full Diagnostic

```python
# Comprehensive check
mcp__diagnostic-mcp__run_full_diagnostic()

# Returns:
# {
#   "timestamp": "2025-12-20T12:00:00Z",
#   "summary": {
#     "total_issues": 5,
#     "critical_issues": 2,
#     "status": "critical"
#   },
#   "port_check": {...},
#   "health_check": {...},
#   "config_check": {...},
#   "tool_check": {...},
#   "recommendations": [
#     "CRITICAL: Resolve port conflicts immediately",
#     "CRITICAL: Restart offline MCP servers"
#   ]
# }
```

## Configuration

### Environment Variables

#### Core Configuration
- `SENTRY_DSN` - Optional Sentry error tracking
- `SENTRY_ENVIRONMENT` - Environment name (default: development)
- `MCP_SSE_PORT` - SSE server port (default: 5583)
- `MCP_SSE_HOST` - SSE server host (default: 0.0.0.0)

#### Authentication (Optional)
- `AUTH_ENABLED` - Enable session token authentication (default: false)
- `AUTH_ADMIN_TOKEN` - Admin token for bootstrapping (required if AUTH_ENABLED=true)
- `AUTH_TOKEN_TTL` - Token TTL in hours (default: 24)
- `AUTH_STORAGE` - Storage backend: `memory` or `supabase` (default: memory)
- `SUPABASE_URL` - Supabase project URL (required if AUTH_STORAGE=supabase)
- `SUPABASE_KEY` - Supabase service role key (required if AUTH_STORAGE=supabase)

**See [AUTHENTICATION.md](docs/AUTHENTICATION.md) for complete authentication setup guide.**

### Settings Path

diagnostic-mcp reads from: `~/.claude/settings.json`

Expected structure:
```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "supergateway", "--sse", "http://localhost:5555/sse"],
      "description": "Server description"
    }
  }
}
```

## Port Range

Expected port range: **5555-5582** (28 ports)

## Response Format

All tools return ResponseEnvelope format:
```json
{
  "ok": true,
  "error": null,
  "message": "Check completed successfully",
  "data": { ... }
}
```

## Logging

Logs written to: `./logs/diagnostic-mcp.log` (or custom path via environment variable)

## Integration

### Claude Code (settings.json)

```json
{
  "mcpServers": {
    "diagnostic-mcp": {
      "command": "npx",
      "args": ["-y", "supergateway", "--sse", "http://localhost:5583/sse"],
      "description": "MCP infrastructure diagnostics and health monitoring"
    }
  }
}
```

## Use Cases

1. **Pre-deployment validation** - Check configuration before deploying new MCP servers
2. **Health monitoring** - Regular checks of all MCP server availability
3. **Troubleshooting** - Diagnose port conflicts, offline servers, configuration issues
4. **Onboarding** - Validate new server integrations
5. **Incident response** - Quick overview of system health during outages

## Limitations

- **check_tool_availability** currently only counts servers
  - Full tool querying requires implementing MCP protocol client
  - Future enhancement to query each server's tool list
- Health checks test HTTP accessibility only
  - Does not validate MCP protocol handshake
  - Does not test tool execution

## Development

### Running Tests

```bash
pytest tests/
```

### Code Style

```bash
black src/
```

### Contributing

Contributions welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Version

Current version: **1.0.0**

See [CHANGELOG.md](CHANGELOG.md) for version history.

## Repository

- **GitHub**: [https://github.com/latvian-lab/diagnostic-mcp](https://github.com/latvian-lab/diagnostic-mcp)
- **Issues**: [https://github.com/latvian-lab/diagnostic-mcp/issues](https://github.com/latvian-lab/diagnostic-mcp/issues)
- **PyPI**: [https://pypi.org/project/diagnostic-mcp/](https://pypi.org/project/diagnostic-mcp/)

## License

MIT License - see [LICENSE](LICENSE) for details.

Copyright (c) 2025 Latvian Lab
