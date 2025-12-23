# Tool Integration Features for diagnostic-mcp

## Overview

Added three critical missing features to diagnostic-mcp that detect when MCP tools are configured but not actually callable. This solves the false positive problem where diagnostics report "all systems operational" but tools fail with "No such tool available" errors.

## Problem Statement

**Before these features:**
- `check_all_health` reported servers as "online" if processes start
- `check_tool_availability` only verified MCP Index registration
- Neither checked if tools could actually be invoked
- Result: False positives - diagnostics pass but tools don't work

**Example failure case (vast-mcp):**
- Server configured in mcp_servers.json ✓
- Process starts successfully ✓
- Health check returns "online" ✓
- But tools fail: "No such tool available" ✗

## New Features

### 1. Tool Callability Check (`check_tool_callability`)

**Purpose:** Verify tools are discoverable and registered, not just configured.

**Implementation:**
- Queries MCP Index database for tool registration
- Cross-references with mcp_servers.json configuration
- Detects servers that are configured but have no indexed tools

**Output Categories:**
- ✅ **Configured AND callable** - Server in config + tools in MCP Index
- ⚠️ **Configured but NOT callable** - Server in config but no tools loaded
- ❌ **Not configured** - Tools in index but server not in config (orphaned)

**Example usage:**
```python
result = mcp__diagnostic-mcp__check_tool_callability(servers=["vast-mcp"])

# Returns:
{
  "configured_and_callable": [...],
  "configured_not_callable": [
    {
      "server": "vast-mcp",
      "reason": "no_tools_loaded",
      "indexed": false
    }
  ],
  "summary": {
    "callable_count": 25,
    "not_callable_count": 1,
    "health": "warning"
  }
}
```

### 2. Namespace Verification (`check_namespace_verification`)

**Purpose:** Verify tools use correct namespace pattern: `mcp__server-name__tool-name`

**Implementation:**
- Queries all tools from MCP Index
- Validates namespace prefix matches server name
- Detects namespace mismatches and conflicts

**Detection:**
- Correct: `mcp__vast-mcp__vast_list_instances`
- Wrong: `mcp__other-server__vast_list_instances`
- Missing: `vast_list_instances` (no prefix)

**Example usage:**
```python
result = mcp__diagnostic-mcp__check_namespace_verification()

# Returns:
{
  "total_tools_checked": 103,
  "correct_namespaces": 102,
  "namespace_issues": [
    {
      "server": "vast-mcp",
      "tool": "mcp__wrong-server__vast_search",
      "issue": "wrong_namespace",
      "expected_prefix": "mcp__vast-mcp__"
    }
  ],
  "summary": {
    "issues_found": 1,
    "health": "warning"
  }
}
```

### 3. Real Invocation Test (`check_real_invocation`)

**Purpose:** Actually call safe test tools from each server to verify end-to-end functionality.

**Implementation:**
- Spawns server subprocess
- Sends MCP protocol initialize request
- Invokes safe read-only tool (list/status commands)
- Parses response to verify tool is callable

**Safe test tools by server:**
- `vast-mcp`: `vast_list_instances(show_all=false)`
- `docker-mcp`: `docker_list_containers(all=false)`
- `knowledge-mcp`: `kb_list(topic="implementations")`
- `github-mcp`: `github_user_get()`
- `system-ops-mcp`: `systemd_list_units()`
- `diagnostic-mcp`: `check_port_consistency()`
- `monitor-mcp`: `http_health_check(url="http://localhost:5555/health")`
- `r2-storage-mcp`: `r2_list_buckets()`
- `sentry-mcp`: `sentry_get_projects()`

**Example usage:**
```python
result = mcp__diagnostic-mcp__check_real_invocation(
    servers=["vast-mcp", "docker-mcp"],
    timeout=10
)

# Returns:
{
  "invocation_results": [
    {
      "server": "vast-mcp",
      "tool": "vast_list_instances",
      "status": "error",
      "error": "No such tool available"
    },
    {
      "server": "docker-mcp",
      "tool": "docker_list_containers",
      "status": "success",
      "response": "tool_callable"
    }
  ],
  "summary": {
    "total_tested": 2,
    "success": 1,
    "error": 1,
    "timeout": 0,
    "health": "warning"
  }
}
```

### 4. Comprehensive Integration Check (`check_tool_integration`)

**Purpose:** Run all three checks and provide unified assessment.

**Implementation:**
- Executes callability, namespace, and invocation checks
- Aggregates results
- Categorizes issues by severity

**Example usage:**
```python
result = mcp__diagnostic-mcp__check_tool_integration()

# Returns:
{
  "timestamp": "2025-12-22T10:30:00Z",
  "overall_health": "warning",
  "issues_found": [
    "1 servers not callable",
    "1 invocation failures"
  ],
  "callability_check": {...},
  "namespace_check": {...},
  "invocation_check": {...},
  "summary": {
    "total_issues": 2,
    "checks_run": 3,
    "status": "warning"
  }
}
```

## Integration with Full Diagnostic

The `run_full_diagnostic` tool now includes tool integration checks:

```python
result = mcp__diagnostic-mcp__run_full_diagnostic()

# Now includes:
{
  "port_check": {...},
  "health_check": {...},
  "config_check": {...},
  "tool_check": {...},
  "integration_check": {...},  # NEW
  "recommendations": [
    "CRITICAL: Fix tool integration issues - configured tools are not callable"
  ]
}
```

## Detection Capabilities

These features detect:

1. **Configuration drift** - Tools configured but not deployed
2. **Index staleness** - MCP Index not reflecting current state
3. **Namespace conflicts** - Tools registered under wrong server
4. **Protocol issues** - Tools that fail to respond to MCP protocol
5. **Deployment failures** - Server starts but tools don't load

## No More False Positives

**Before:**
- Health check: "online" ✓
- Tool availability: "available" ✓
- **Actual invocation: FAILS** ✗

**After:**
- Health check: "online" ✓
- Tool availability: "available" ✓
- **Integration check: "warning - 1 server not callable"** ✓
- **Real invocation: "error - No such tool available"** ✓
- **Recommendation: "CRITICAL: Fix tool integration issues"** ✓

## Success Criteria (Met)

✅ Diagnostic detects when tools are configured but not callable
✅ Clear output showing which servers have invocation problems
✅ No false positives - "all systems operational" only when tools actually work
✅ Integration with full diagnostic report
✅ Safe test tools defined for major MCP servers
✅ Comprehensive test coverage

## Usage Examples

### Quick check single server
```bash
mcp__diagnostic-mcp__check_tool_callability(servers=["vast-mcp"])
```

### Verify all namespaces
```bash
mcp__diagnostic-mcp__check_namespace_verification()
```

### Test tool invocation
```bash
mcp__diagnostic-mcp__check_real_invocation(timeout=10)
```

### Full integration assessment
```bash
mcp__diagnostic-mcp__check_tool_integration()
```

### Include in full diagnostic
```bash
mcp__diagnostic-mcp__run_full_diagnostic()
```

## Testing

All features have comprehensive test coverage:

```bash
cd /srv/latvian_mcp/servers/diagnostic-mcp
PYTHONPATH=src:$PYTHONPATH pytest tests/test_tool_integration.py -v

# Results:
# ✓ test_no_supabase_connection
# ✓ test_with_mock_supabase
# ✓ test_namespace_verification
# ✓ test_real_invocation
# ✓ test_integration_combines_all_checks
```

## Files Modified

- `src/diagnostic_mcp/server.py` - Added 4 new tools and handlers
- `tests/test_tool_integration.py` - New test suite

## Architecture

```
check_tool_integration()
├── check_tool_callability()
│   ├── Query MCP Index (Supabase)
│   ├── Cross-reference mcp_servers.json
│   └── Categorize: callable vs not callable
│
├── check_namespace_verification()
│   ├── Query all tools from MCP Index
│   ├── Validate namespace patterns
│   └── Detect mismatches
│
└── check_real_invocation()
    ├── Spawn server subprocess
    ├── Send MCP initialize request
    ├── Invoke safe test tool
    └── Parse response (success/error/timeout)
```

## Future Enhancements

- Add HTTP transport support for invocation tests
- Expand safe test tool library
- Add performance metrics (response time per tool)
- Create auto-remediation suggestions
- Integration with Sentry for alerting

## Version

- **Initial implementation:** 2025-12-22
- **diagnostic-mcp version:** 0.3.0+
- **Spec compliance:** LATVIAN_LAB_MCP_MASTER_SPEC_v1.2 § 7.X
