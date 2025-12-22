# Health Probes Documentation

## Overview

The diagnostic-mcp HTTP server implements Kubernetes-style health probes (startup, liveness, readiness) with enhanced diagnostic capabilities. This document describes the probe logic, configuration options, and integration patterns.

## Probe Types

### 1. Startup Probe

**Purpose:** Indicates when the server has completed its initialization phase.

**Status:**
- `DOWN` - Server is still starting up
- `UP` - Startup duration has elapsed, server is initialized

**Use Case:** Use this probe to delay traffic routing until the server is fully initialized. Prevents premature requests during startup.

**Configuration:**
- `--startup-duration N` - Startup phase duration in seconds (default: 30)

**Endpoints:**
- HTTP: `GET /health?startup` or `GET /health/startup`
- MCP: Not available (HTTP server specific)
- CLI: `python cli.py --check probes`

**Response Example:**
```json
{
  "status": "UP",
  "timestamp": "2025-12-21T20:00:00.123456",
  "uptime_seconds": 35.42,
  "startup_duration_seconds": 30.0,
  "startup_complete": true
}
```

### 2. Liveness Probe

**Purpose:** Indicates if the server is alive and not deadlocked.

**Status:**
- `UP` - Server is alive and responding
- `DOWN` - Critical failure detected (10+ consecutive failures)

**Use Case:** Use this probe to detect when the server needs to be restarted. A DOWN status indicates a critical problem that requires intervention.

**Configuration:**
- Hardcoded threshold: 10 consecutive failures

**Endpoints:**
- HTTP: `GET /health?live`
- MCP: `check_liveness_probe` tool
- CLI: `python cli.py --check liveness`

**Response Example:**
```json
{
  "status": "UP",
  "timestamp": "2025-12-21T20:00:00.123456",
  "uptime_seconds": 120.5,
  "last_health_check": "2025-12-21T19:59:59.000000",
  "consecutive_failures": 0
}
```

**Critical Failure Example:**
```json
{
  "status": "DOWN",
  "timestamp": "2025-12-21T20:00:00.123456",
  "uptime_seconds": 120.5,
  "last_health_check": "2025-12-21T19:59:59.000000",
  "consecutive_failures": 10,
  "reason": "critical_failure_threshold_exceeded",
  "message": "Server has 10 consecutive failures"
}
```

### 3. Readiness Probe

**Purpose:** Indicates if the server is ready to accept traffic.

**Status:**
- `DOWN` - Server is not ready (startup incomplete or too many rejections)
- `UP` - Server is ready to accept traffic
- `UP` with `degraded: true` - Server is ready but experiencing issues

**Use Case:** Use this probe for load balancer health checks. The server may be alive (liveness UP) but not ready to accept traffic (readiness DOWN).

**Configuration:**
- `--allowed-rejections N` - Max rejections before unready (default: 100)
- `--sampling-interval N` - Sampling window in seconds (default: 10)
- `--recovery-interval N` - Recovery time in seconds (default: 2x sampling)
- `--degraded-threshold F` - Error rate for degraded state (default: 0.25)

**Endpoints:**
- HTTP: `GET /health?ready`
- MCP: `check_readiness_probe` tool
- CLI: `python cli.py --check readiness`

**Response Example (Ready):**
```json
{
  "status": "UP",
  "timestamp": "2025-12-21T20:00:00.123456",
  "degraded": false,
  "metrics": {
    "total_requests": 1000,
    "failed_requests": 50,
    "current_rejections": 5,
    "rejection_threshold": 100,
    "error_rate": 0.05,
    "degraded_threshold": 0.25
  },
  "uptime_seconds": 300.0
}
```

**Response Example (Degraded):**
```json
{
  "status": "UP",
  "timestamp": "2025-12-21T20:00:00.123456",
  "degraded": true,
  "metrics": {
    "total_requests": 1000,
    "failed_requests": 300,
    "current_rejections": 25,
    "rejection_threshold": 100,
    "error_rate": 0.30,
    "degraded_threshold": 0.25
  },
  "uptime_seconds": 300.0,
  "message": "Server degraded: 30.0% error rate"
}
```

**Response Example (Unready - Rejections):**
```json
{
  "status": "DOWN",
  "timestamp": "2025-12-21T20:00:00.123456",
  "degraded": false,
  "metrics": {
    "total_requests": 1000,
    "failed_requests": 150,
    "current_rejections": 120,
    "rejection_threshold": 100,
    "error_rate": 0.15,
    "degraded_threshold": 0.25
  },
  "uptime_seconds": 300.0,
  "unready_since": "2025-12-21T19:58:00.000000",
  "recovery_in_seconds": 15.5,
  "reason": "rejection_threshold_exceeded"
}
```

## Comprehensive Probe Status

**Purpose:** Get all probe states in a single request with overall health assessment.

**Overall Status Values:**
- `healthy` - All probes UP, no degradation
- `starting` - Startup probe DOWN, server initializing
- `degraded` - Ready but experiencing issues (high error rate)
- `unready` - Readiness probe DOWN, server not accepting traffic
- `critical` - Liveness probe DOWN, server needs restart

**Priority Order:** `critical` > `starting` > `unready` > `degraded` > `healthy`

**Endpoints:**
- HTTP: `GET /health?status` or `GET /health/status`
- MCP: `get_probe_status` tool
- CLI: `python cli.py --check probes`

**Response Example:**
```json
{
  "overall_status": "healthy",
  "timestamp": "2025-12-21T20:00:00.123456",
  "probes": {
    "startup": {
      "status": "UP",
      "startup_complete": true,
      "uptime_seconds": 300.0
    },
    "liveness": {
      "status": "UP",
      "consecutive_failures": 0
    },
    "readiness": {
      "status": "UP",
      "degraded": false,
      "metrics": {
        "error_rate": 0.05
      }
    }
  },
  "summary": {
    "startup_complete": true,
    "is_live": true,
    "is_ready": true,
    "is_degraded": false,
    "uptime_seconds": 300.0
  }
}
```

## Configuration Guide

### Default Configuration

```bash
python http_server.py
```

**Defaults:**
- Startup duration: 30 seconds
- Allowed rejections: 100 per sampling interval
- Sampling interval: 10 seconds
- Recovery interval: 20 seconds (2x sampling)
- Degraded threshold: 0.25 (25% error rate)

### Custom Configuration

```bash
python http_server.py \
  --port 5555 \
  --startup-duration 60 \
  --allowed-rejections 50 \
  --sampling-interval 5 \
  --recovery-interval 15 \
  --degraded-threshold 0.10
```

**Configuration Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--startup-duration` | 30 | Startup phase duration (seconds) |
| `--allowed-rejections` | 100 | Max rejections before unready |
| `--sampling-interval` | 10 | Rejection sampling window (seconds) |
| `--recovery-interval` | 20 | Time to wait before marking ready again (seconds) |
| `--degraded-threshold` | 0.25 | Error rate threshold for degraded state (0.0-1.0) |

### Environment Variables

```bash
export MCP_HTTP_PORT=5555
export MCP_HTTP_HOST=0.0.0.0
```

## Integration Patterns

### 1. Kubernetes Integration

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: diagnostic-mcp
spec:
  containers:
  - name: diagnostic-mcp
    image: diagnostic-mcp:latest
    ports:
    - containerPort: 5555
    startupProbe:
      httpGet:
        path: /health/startup
        port: 5555
      failureThreshold: 30
      periodSeconds: 1
    livenessProbe:
      httpGet:
        path: /health?live
        port: 5555
      failureThreshold: 3
      periodSeconds: 10
    readinessProbe:
      httpGet:
        path: /health?ready
        port: 5555
      failureThreshold: 3
      periodSeconds: 5
```

### 2. Load Balancer Integration

**NGINX:**
```nginx
upstream diagnostic_mcp {
    server localhost:5555 max_fails=3 fail_timeout=30s;
    check interval=5000 rise=2 fall=3 timeout=1000 type=http;
    check_http_send "GET /health?ready HTTP/1.0\r\n\r\n";
    check_http_expect_alive http_2xx;
}
```

**HAProxy:**
```haproxy
backend diagnostic_mcp
    option httpchk GET /health?ready
    http-check expect status 200
    server server1 localhost:5555 check inter 5s fall 3 rise 2
```

### 3. MCP Client Integration

```python
from mcp import ClientSession

async with ClientSession(server) as session:
    # Check readiness before making requests
    result = await session.call_tool(
        "check_readiness_probe",
        arguments={}
    )

    probe_data = result[0].text
    if probe_data["data"]["status"] == "UP":
        # Server is ready, proceed with requests
        pass
    else:
        # Server not ready, handle accordingly
        pass
```

### 4. CLI Monitoring

```bash
# Check all probes
python cli.py --check probes

# Check readiness only
python cli.py --check readiness --format json

# Continuous monitoring
while true; do
  python cli.py --check probes --format summary
  sleep 5
done
```

### 5. Prometheus Integration

Create a custom exporter that queries probe status:

```python
from prometheus_client import Gauge, start_http_server
import requests
import time

# Define metrics
startup_probe = Gauge('diagnostic_mcp_startup_probe', 'Startup probe status (1=UP, 0=DOWN)')
liveness_probe = Gauge('diagnostic_mcp_liveness_probe', 'Liveness probe status (1=UP, 0=DOWN)')
readiness_probe = Gauge('diagnostic_mcp_readiness_probe', 'Readiness probe status (1=UP, 0=DOWN)')
degraded_state = Gauge('diagnostic_mcp_degraded', 'Degraded state (1=degraded, 0=normal)')
error_rate = Gauge('diagnostic_mcp_error_rate', 'Current error rate')

def collect_metrics():
    try:
        response = requests.get('http://localhost:5555/health?status')
        data = response.json()

        probes = data['probes']
        startup_probe.set(1 if probes['startup']['status'] == 'UP' else 0)
        liveness_probe.set(1 if probes['liveness']['status'] == 'UP' else 0)
        readiness_probe.set(1 if probes['readiness']['status'] == 'UP' else 0)
        degraded_state.set(1 if probes['readiness'].get('degraded', False) else 0)
        error_rate.set(probes['readiness']['metrics']['error_rate'])
    except Exception as e:
        print(f"Error collecting metrics: {e}")

if __name__ == '__main__':
    start_http_server(9090)
    while True:
        collect_metrics()
        time.sleep(5)
```

## Troubleshooting

### Startup Probe Stuck on DOWN

**Symptom:** Startup probe remains DOWN even after expected duration.

**Cause:** Server started with long `--startup-duration`.

**Solution:**
```bash
# Check current configuration
curl http://localhost:5555/info | jq '.health_config'

# Restart with shorter duration
python http_server.py --startup-duration 10
```

### Readiness Probe Flapping

**Symptom:** Readiness probe alternates between UP and DOWN frequently.

**Cause:** Rejection threshold too low or sampling interval too short.

**Solution:**
```bash
# Increase allowed rejections and sampling interval
python http_server.py --allowed-rejections 200 --sampling-interval 30
```

### Degraded State Persistent

**Symptom:** Readiness shows `degraded: true` consistently.

**Cause:** Error rate above degraded threshold.

**Solution:**
1. Investigate why requests are failing
2. Adjust threshold if current error rate is acceptable:
```bash
python http_server.py --degraded-threshold 0.40  # 40% error rate
```

### Liveness Probe DOWN

**Symptom:** Liveness probe status is DOWN.

**Cause:** 10+ consecutive failures detected (critical issue).

**Solution:**
1. Check server logs for errors
2. Restart the server
3. Investigate root cause of failures

```bash
# Check logs
tail -n 100 /srv/latvian_mcp/logs/diagnostic-mcp.log

# Restart server
systemctl restart diagnostic-mcp-http
```

## Testing

Run the comprehensive test suite:

```bash
cd /srv/latvian_mcp/servers/diagnostic-mcp
python -m pytest tests/test_health_monitor.py -v
```

**Test Coverage:**
- Initialization with default/custom configuration
- Startup probe transitions
- Liveness probe failure detection and recovery
- Readiness probe rejection tracking
- Degraded state detection
- Recovery after unready state
- Metadata completeness
- Overall status calculation

## Implementation Details

### HealthMonitor Class

Located in: `/srv/latvian_mcp/servers/diagnostic-mcp/http_server.py`

**Key Methods:**
- `record_request(success: bool)` - Record request outcome
- `get_startup_status() -> Dict` - Get startup probe status
- `get_liveness() -> Dict` - Get liveness probe status
- `get_readiness() -> Dict` - Get readiness probe status
- `get_probe_status() -> Dict` - Get comprehensive status

**State Tracking:**
- `server_start_time` - When server started
- `rejection_count` - Current rejections in sampling window
- `total_requests` - Total requests processed
- `failed_requests` - Total failed requests
- `failure_count` - Consecutive failures (for liveness)
- `is_ready` - Current readiness state
- `is_live` - Current liveness state
- `unready_since` - When server became unready

### HTTP Endpoints

All endpoints return JSON with appropriate HTTP status codes:
- `200 OK` - Probe is UP
- `503 Service Unavailable` - Probe is DOWN

**Query Parameter Routes:**
- `/health` - Basic health check (always 200)
- `/health?live` - Liveness probe
- `/health?ready` - Readiness probe
- `/health?startup` - Startup probe
- `/health?status` - Comprehensive status

**Direct Routes:**
- `/health/startup` - Startup probe
- `/health/status` - Comprehensive status

### MCP Tools

Three tools exposed via MCP protocol:

1. **check_readiness_probe**
   - No arguments
   - Returns readiness probe status

2. **check_liveness_probe**
   - No arguments
   - Returns liveness probe status

3. **get_probe_status**
   - No arguments
   - Returns comprehensive probe status

## Version History

- **v2.1.0** (2025-12-21)
  - Enhanced HealthMonitor with full probe logic
  - Added degraded state detection
  - Improved overall status calculation
  - Comprehensive test coverage
  - Documentation complete

- **v2.0.0** (2025-12-20)
  - Initial probe implementation
  - Basic startup, liveness, readiness probes
  - HTTP server integration
