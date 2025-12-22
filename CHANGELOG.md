# Changelog

All notable changes to diagnostic-mcp are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-12-21

### Added

#### Phase 1 - Core Infrastructure

- **HTTP/SSE Server with Systemd Service**
  - Uvicorn-based HTTP server for SSE transport
  - Direct stdio MCP mode for Claude Code integration
  - Systemd service file (`diagnostic-mcp-http.service`) for automatic startup
  - CORS middleware for Docker network access
  - Configurable host and port via CLI arguments and environment variables

- **Kubernetes-Style Health Probes**
  - Startup probe: Delays traffic until server initialization complete
  - Liveness probe: Detects critical failures requiring restart
  - Readiness probe: Tracks rejection rates and determines request acceptance
  - Configurable probe thresholds via CLI options and environment variables
  - HTTP endpoints for probe status: `/health`, `/health/startup`, `/health/status`

- **Session Token Authentication (Optional)**
  - Bearer token validation for API endpoints
  - Dual storage backends: in-memory and Supabase
  - Token TTL configuration (default: 24 hours)
  - Admin token for bootstrapping authentication system
  - Secure token generation with cryptographic hashing

- **Supabase Integration**
  - Historical tracking of authentication tokens
  - Session token storage and validation
  - Audit trail for security events
  - Service role key authentication to Supabase

- **Comprehensive Logging and Error Tracking**
  - Structured logging to `/srv/latvian_mcp/logs/diagnostic-mcp.log`
  - Sentry integration for error monitoring
  - Environment-based configuration (development, staging, production)
  - Detailed error messages with context

#### Phase 2 - Enhanced Features

- **Configuration Export Formats**
  - JSON export of diagnostic results
  - YAML export for configuration review
  - Markdown export for documentation generation
  - Human-readable text formatting with color support

- **Multi-Transport Testing**
  - SSE endpoint health monitoring
  - Configurable timeout for health checks (default: 5 seconds)
  - Parallel health checks using asyncio for performance
  - Response time measurement per server

- **Enhanced CLI with --quick Mode and --timeout Options**
  - Quick diagnostics mode: `python cli.py --quick`
  - Custom timeout support: `python cli.py --timeout 10`
  - Format selection: `--format json|yaml|text|summary`
  - Output filtering: `--check port|health|config|tools|all`
  - Quiet mode: `--quiet` for minimal output

- **Performance Optimizations**
  - Concurrent health checks via asyncio.gather
  - Non-blocking I/O for HTTP requests
  - Efficient port mapping extraction from settings.json
  - Caching of server configurations

#### Phase 3 - Production Readiness

- **Advanced Probe Logic with Degraded State Detection**
  - Rejection threshold tracking with sampling intervals
  - Recovery interval configuration for hysteresis
  - Degraded state detection based on error rate thresholds
  - Automatic recovery tracking after failures
  - Status priority system: critical > starting > unready > degraded > healthy

- **Historical Trend Analysis**
  - Probe status history tracked over time
  - Uptime percentage calculation
  - Failure count tracking per probe
  - Timestamps for all status transitions
  - Recovery interval statistics

- **Complete Test Coverage (68 Tests)**
  - Unit tests for all diagnostic functions
  - Integration tests for probe state transitions
  - Health check endpoint testing
  - Configuration validation tests
  - Error handling and recovery tests
  - 100% pass rate with comprehensive coverage reports

- **Production-Ready Authentication**
  - Session token management with TTL enforcement
  - Bearer token validation on all authenticated endpoints
  - Token revocation support
  - Audit logging for all authentication events
  - Support for both in-memory and persistent (Supabase) storage

- **Five Diagnostic Tools**
  - `check_port_consistency`: Validates MCP server port assignments
  - `check_all_health`: Tests all MCP server SSE endpoints in parallel
  - `check_configurations`: Validates settings.json format consistency
  - `check_tool_availability`: Inventories registered MCP servers
  - `run_full_diagnostic`: Comprehensive diagnostic report with recommendations
  - `check_readiness_probe`: Query readiness probe status
  - `check_liveness_probe`: Query liveness probe status
  - `get_probe_status`: Comprehensive probe status with metrics

### Features Details

#### Diagnostic Tools

**check_port_consistency**
- Parses ~/.claude/settings.json for all MCP server configurations
- Extracts port assignments from supergateway args
- Detects port conflicts (multiple servers on same port)
- Identifies gaps in expected port range (5555-5582)
- Flags servers without port assignments
- Returns structured conflict report with recommendations

**check_all_health**
- Performs parallel health checks on all configured MCP servers
- Tests HTTP accessibility of SSE endpoints
- Measures response time per server
- Categorizes servers: online, offline, error
- Configurable timeout per request (default: 5 seconds)
- Returns detailed status with response times

**check_configurations**
- Validates settings.json format consistency across all servers
- Verifies use of npx with supergateway
- Checks for required --sse flag and URL format
- Validates description field presence
- Reports specific issues per misconfigured server
- Aggregates results with consistency metrics

**check_tool_availability**
- Inventories all registered MCP servers
- Counts total server count
- Lists server names
- Foundation for future tool querying via MCP protocol

**run_full_diagnostic**
- Executes all four checks in sequence
- Aggregates results with unified timestamp
- Calculates total and critical issue counts
- Determines overall status: critical, warning, or healthy
- Generates actionable recommendations
- Returns comprehensive report suitable for incident response

**Probe Status Tools**
- `check_readiness_probe`: Returns readiness status with rejection metrics
- `check_liveness_probe`: Returns liveness status with failure tracking
- `get_probe_status`: Returns all probes plus overall system health

#### Health Probe Implementation

**Startup Probe**
- Initially DOWN during startup duration (configurable, default: 30 seconds)
- Transitions to UP after startup period completes
- Use case: Prevent traffic until service fully initialized

**Liveness Probe**
- Tracks consecutive failures (hardcoded threshold: 10)
- DOWN after 10 consecutive failures
- Automatically recovers when failures stop
- Use case: Detect critical failures requiring restart

**Readiness Probe**
- Tracks rejection count with sampling intervals
- DOWN if rejections exceed threshold within interval
- Degraded state when error rate exceeds threshold
- Automatic recovery after configured interval
- Use case: Load balancer health checks and traffic routing

**Overall Status Priority**
- CRITICAL: Liveness probe DOWN
- STARTING: Startup probe DOWN
- UNREADY: Readiness probe DOWN
- DEGRADED: High error rate but still accepting traffic
- HEALTHY: All probes UP with low error rate

### Configuration

- HTTP server port: Configurable via `--port` or `MCP_HTTP_PORT` (default: 5555)
- HTTP server host: Configurable via `--host` or `MCP_HTTP_HOST` (default: 0.0.0.0)
- SSE server port: Configurable via `--sse-port` or `MCP_SSE_PORT` (default: 5583)
- Startup duration: `--startup-duration` (default: 30 seconds)
- Allowed rejections: `--allowed-rejections` (default: 100)
- Sampling interval: `--sampling-interval` (default: 10 seconds)
- Recovery interval: `--recovery-interval` (default: 2x sampling interval)
- Degraded threshold: `--degraded-threshold` (default: 0.25 or 25%)
- Health check timeout: `--timeout` (default: 5 seconds)

### Dependencies

- mcp >= 0.9.0: MCP protocol implementation
- requests >= 2.31.0: HTTP client for health checks
- httpx >= 0.27.0: Async HTTP client
- uvicorn >= 0.27.0: ASGI server
- starlette >= 0.36.0: Web framework
- supabase >= 2.0.0: Supabase database client
- sentry-sdk >= 2.0.0: Error monitoring and tracking

### Documentation

- **README.md**: Complete feature overview and usage guide
- **HEALTH_PROBES.md**: Comprehensive probe documentation with integration patterns
- **IMPLEMENTATION_SUMMARY.md**: Detailed implementation notes
- **PHASE3_COMPLETION_SUMMARY.md**: Phase 3 deliverables and testing results
- **INSTALL_HTTP_SERVER.md**: HTTP server setup and configuration guide
- **TRENDS_IMPLEMENTATION.md**: Historical trend tracking implementation
- **PERFORMANCE_TEST_RESULTS.md**: Performance benchmarks and optimization results
- **.env.example**: Environment variable template

### Testing

- pytest-based test suite with 68 comprehensive tests
- Unit tests for all diagnostic functions
- Integration tests for probe state transitions
- Health endpoint tests
- Configuration validation tests
- Error handling and recovery scenarios
- All tests passing with 100% success rate

### Deployment

- Systemd service file for automatic startup
- Docker-compatible CORS configuration
- Logging to centralized location: `/srv/latvian_mcp/logs/diagnostic-mcp.log`
- Production-ready error handling and monitoring

## Compatibility

- Python 3.11+
- Linux systems with systemd support
- Docker networks (CORS enabled)
- Kubernetes health probe standards
- Load balancer integration (HAProxy, NGINX, etc.)

## Known Limitations

- `check_tool_availability` currently inventories servers only
  - Full tool querying requires implementing MCP protocol client
  - Future enhancement planned for Phase 4

- Health checks test HTTP accessibility only
  - Does not validate MCP protocol handshake
  - Does not test tool execution
  - Suitable for availability monitoring, not functional testing

## Security

- Optional session token authentication
- Bearer token validation
- Secure token generation with cryptographic hashing
- Audit trail support via Supabase
- Sentry monitoring for security events

## License

Internal tool for Latvian Lab infrastructure.

---

**Release Date:** 2025-12-21
**Status:** Production Ready
**Test Coverage:** 68/68 tests passing
**Documentation:** Complete
