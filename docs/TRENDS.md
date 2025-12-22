# Diagnostic Trends Analysis

This document describes the trend analysis features available in diagnostic-mcp for analyzing historical health data and detecting server degradations.

## Overview

Diagnostic-mcp stores health check results in Supabase (`diagnostic_history` table) and provides powerful trend analysis capabilities to:

- Track server uptime over time
- Detect degradations (servers going from healthy → unhealthy)
- Identify improvements (servers recovering)
- Compare metrics between time periods
- Calculate response time trends

## Concepts

### Time Windows

All trend analysis queries use time window strings:

- `1h` - Last hour
- `24h` - Last 24 hours (default)
- `7d` - Last 7 days
- `30d` - Last 30 days

### Metrics

**Uptime Percentage**
- Percentage of checks where server was online
- Formula: `(online_checks / total_checks) * 100`
- Range: 0-100%

**Failure Rate**
- Percentage of checks that resulted in errors
- Formula: `(error_checks / total_checks) * 100`
- Range: 0-100%

**Response Time Statistics**
- Mean: Average response time across all checks
- P50: 50th percentile (median)
- P95: 95th percentile
- P99: 99th percentile

**Status Changes**
- Count of transitions between online and offline states
- Indicates stability (fewer changes = more stable)

**Degradation Score**
- Linear regression slope of uptime over time
- Positive = improving, Negative = degrading
- Threshold: |slope| > 1 indicates significant trend

## Available Tools

### 1. `analyze_health_trends`

Analyze overall health trends over a time window.

**MCP Tool:**
```json
{
  "name": "analyze_health_trends",
  "arguments": {
    "time_window": "24h",
    "server_filter": "optional-server-name"
  }
}
```

**HTTP Endpoint:**
```bash
GET /trends?window=24h
```

**Returns:**
```json
{
  "time_window": "24h",
  "server_filter": null,
  "total_records": 48,
  "metrics": {
    "uptime_percentage": 95.5,
    "failure_rate": 2.3,
    "response_time": {
      "mean": 125.4,
      "p50": 98.2,
      "p95": 289.7,
      "p99": 450.1,
      "count": 200
    },
    "status_changes": {
      "online_to_offline": 3,
      "offline_to_online": 2,
      "total_transitions": 5
    },
    "degradation_score": -0.5,
    "trend_direction": "stable"
  },
  "first_record": "2025-12-20T12:00:00Z",
  "last_record": "2025-12-21T12:00:00Z"
}
```

### 2. `get_server_history`

Get historical health checks for a specific server.

**MCP Tool:**
```json
{
  "name": "get_server_history",
  "arguments": {
    "server_name": "knowledge-mcp",
    "time_window": "7d"
  }
}
```

**HTTP Endpoint:**
```bash
GET /trends/knowledge-mcp?window=7d
```

**Returns:**
```json
{
  "server_name": "knowledge-mcp",
  "time_window": "7d",
  "total_checks": 168,
  "uptime_percentage": 98.2,
  "response_time_stats": {
    "mean": 145.2,
    "p50": 120.5,
    "p95": 312.8,
    "p99": 489.3,
    "count": 165
  },
  "history": [
    {
      "timestamp": "2025-12-20T12:00:00Z",
      "status": "online",
      "response_time_ms": 125.4,
      "transport": "stdio",
      "error": null,
      "note": null
    }
  ],
  "first_check": "2025-12-14T12:00:00Z",
  "last_check": "2025-12-21T12:00:00Z"
}
```

### 3. `detect_degradations`

Detect servers with declining uptime.

**MCP Tool:**
```json
{
  "name": "detect_degradations",
  "arguments": {
    "time_window": "7d",
    "threshold": 20.0
  }
}
```

**HTTP Endpoint:**
```bash
GET /trends/degradations?window=7d&threshold=20.0
```

**Returns:**
```json
{
  "time_window": "7d",
  "threshold": 20.0,
  "total_servers_analyzed": 15,
  "degraded_servers_count": 2,
  "degraded_servers": [
    {
      "server_name": "example-mcp",
      "first_period_uptime": 95.0,
      "second_period_uptime": 65.0,
      "decline_percentage": 30.0,
      "severity": "warning"
    },
    {
      "server_name": "another-mcp",
      "first_period_uptime": 98.0,
      "second_period_uptime": 45.0,
      "decline_percentage": 53.0,
      "severity": "critical"
    }
  ],
  "period_split": {
    "first_period_records": 84,
    "second_period_records": 84
  }
}
```

**Severity Levels:**
- `warning`: Decline 20-49%
- `critical`: Decline ≥50%

### 4. `compare_time_periods`

Compare metrics between two time periods.

**MCP Tool:**
```json
{
  "name": "compare_time_periods",
  "arguments": {
    "period1_start": "2025-12-14T00:00:00Z",
    "period1_end": "2025-12-15T00:00:00Z",
    "period2_start": "2025-12-20T00:00:00Z",
    "period2_end": "2025-12-21T00:00:00Z"
  }
}
```

**HTTP Endpoint:**
```bash
GET /trends/compare?p1_start=2025-12-14T00:00:00Z&p1_end=2025-12-15T00:00:00Z&p2_start=2025-12-20T00:00:00Z&p2_end=2025-12-21T00:00:00Z
```

**Returns:**
```json
{
  "period1": {
    "start": "2025-12-14T00:00:00Z",
    "end": "2025-12-15T00:00:00Z",
    "records": 48,
    "uptime_percentage": 92.5,
    "failure_rate": 3.2,
    "response_time": {
      "mean": 156.3,
      "p50": 125.4,
      "p95": 345.2,
      "p99": 512.8,
      "count": 185
    },
    "status_changes": {
      "online_to_offline": 5,
      "offline_to_online": 4,
      "total_transitions": 9
    }
  },
  "period2": {
    "start": "2025-12-20T00:00:00Z",
    "end": "2025-12-21T00:00:00Z",
    "records": 48,
    "uptime_percentage": 96.8,
    "failure_rate": 1.5,
    "response_time": {
      "mean": 134.2,
      "p50": 112.3,
      "p95": 298.7,
      "p99": 445.1,
      "count": 192
    },
    "status_changes": {
      "online_to_offline": 2,
      "offline_to_online": 2,
      "total_transitions": 4
    }
  },
  "comparison": {
    "uptime_delta": 4.3,
    "failure_rate_delta": -1.7,
    "response_time_delta": -22.1,
    "overall_trend": "stable"
  }
}
```

**Trend Determination:**
- `improving`: uptime_delta > 5%
- `degrading`: uptime_delta < -5%
- `stable`: -5% ≤ uptime_delta ≤ 5%

## Interpretation Guide

### Uptime Percentage

| Range | Status | Action |
|-------|--------|--------|
| 99-100% | Excellent | No action needed |
| 95-98% | Good | Monitor for patterns |
| 90-94% | Warning | Investigate failures |
| <90% | Critical | Immediate action required |

### Failure Rate

| Range | Status | Action |
|-------|--------|--------|
| 0-1% | Normal | No action needed |
| 1-5% | Elevated | Monitor closely |
| 5-10% | High | Investigate root cause |
| >10% | Critical | Immediate action required |

### Response Time

| Percentile | Use Case |
|------------|----------|
| P50 (Median) | Typical user experience |
| P95 | Most users' worst experience |
| P99 | Edge cases, debugging |

**Alerts:**
- P50 > 200ms: Consider optimization
- P95 > 500ms: Performance issues
- P99 > 1000ms: Critical slowdowns

### Degradation Score

| Score | Trend | Interpretation |
|-------|-------|----------------|
| > 1.0 | Improving | Uptime increasing over time |
| -1.0 to 1.0 | Stable | No significant trend |
| < -1.0 | Degrading | Uptime decreasing over time |

**Recommended Actions:**
- Score < -2.0: Investigate immediately
- Score < -5.0: Critical - plan intervention
- Score < -10.0: Emergency - uptime collapsing

## Example Queries

### Check Last 24 Hours

**MCP:**
```json
{
  "name": "analyze_health_trends",
  "arguments": {
    "time_window": "24h"
  }
}
```

**HTTP:**
```bash
curl http://localhost:5583/trends?window=24h
```

### Server History for Last Week

**MCP:**
```json
{
  "name": "get_server_history",
  "arguments": {
    "server_name": "github-mcp",
    "time_window": "7d"
  }
}
```

**HTTP:**
```bash
curl http://localhost:5583/trends/github-mcp?window=7d
```

### Find Degraded Servers

**MCP:**
```json
{
  "name": "detect_degradations",
  "arguments": {
    "time_window": "30d",
    "threshold": 15.0
  }
}
```

**HTTP:**
```bash
curl "http://localhost:5583/trends/degradations?window=30d&threshold=15.0"
```

### Compare This Week vs Last Week

**MCP:**
```json
{
  "name": "compare_time_periods",
  "arguments": {
    "period1_start": "2025-12-14T00:00:00Z",
    "period1_end": "2025-12-21T00:00:00Z",
    "period2_start": "2025-12-07T00:00:00Z",
    "period2_end": "2025-12-14T00:00:00Z"
  }
}
```

**HTTP:**
```bash
curl "http://localhost:5583/trends/compare?p1_start=2025-12-14T00:00:00Z&p1_end=2025-12-21T00:00:00Z&p2_start=2025-12-07T00:00:00Z&p2_end=2025-12-14T00:00:00Z"
```

## Data Requirements

### Minimum Data for Trends

- **analyze_health_trends**: At least 1 record
- **get_server_history**: At least 1 record mentioning the server
- **detect_degradations**: At least 4 records (split into halves)
- **compare_time_periods**: At least 1 record in each period

### Recommended Data Volume

For reliable trend analysis:
- Run health checks every 30 minutes
- Retain at least 30 days of history
- Clean up old data beyond retention period

**Example schedule:**
```bash
# Add to cron (every 30 minutes)
*/30 * * * * /path/to/diagnostic-mcp --check health --save-history
```

## Error Handling

### No Data Available

```json
{
  "ok": false,
  "error": "no_data",
  "message": "No historical data found for time window: 24h",
  "data": {
    "time_window": "24h",
    "server_filter": null,
    "total_records": 0
  }
}
```

**Solution:** Run health checks with `--save-history` to populate data.

### Insufficient Data

```json
{
  "ok": false,
  "error": "insufficient_data",
  "message": "Insufficient data to detect degradations (need at least 4 records)",
  "data": {
    "time_window": "24h",
    "threshold": 20.0,
    "total_records": 2
  }
}
```

**Solution:** Wait for more health checks to accumulate or reduce time window.

### Invalid Time Window

```json
{
  "ok": false,
  "error": "analysis_failed",
  "message": "Failed to analyze health trends: Invalid time window format: 24hours",
  "data": null
}
```

**Solution:** Use valid format like `24h` or `7d`.

## Performance Considerations

### Query Optimization

- **Short windows (1h, 24h)**: Very fast (<100ms)
- **Medium windows (7d)**: Fast (<500ms)
- **Long windows (30d)**: May take 1-2 seconds

### Database Indexes

Ensure these indexes exist in Supabase:

```sql
CREATE INDEX idx_diagnostic_history_created_at
ON diagnostic_history(created_at DESC);

CREATE INDEX idx_diagnostic_history_status
ON diagnostic_history(status);
```

### Caching Recommendations

For frequently accessed trends:
- Cache results for 5-15 minutes
- Invalidate cache on new health check
- Use Redis or in-memory cache

## Integration Examples

### Monitoring Dashboard

```python
import asyncio
from diagnostic_mcp import trends

async def dashboard_metrics():
    # Get current trends
    current = await trends.analyze_health_trends(time_window="24h")

    # Detect degradations
    degraded = await trends.detect_degradations(
        time_window="7d",
        threshold=20.0
    )

    # Build dashboard data
    dashboard = {
        "uptime": current["data"]["metrics"]["uptime_percentage"],
        "response_time": current["data"]["metrics"]["response_time"]["p95"],
        "degraded_count": degraded["data"]["degraded_servers_count"],
        "trend": current["data"]["metrics"]["trend_direction"]
    }

    return dashboard
```

### Alerting System

```python
async def check_for_alerts():
    # Detect degradations
    result = await trends.detect_degradations(
        time_window="24h",
        threshold=30.0
    )

    if result["ok"]:
        degraded = result["data"]["degraded_servers"]

        for server in degraded:
            if server["severity"] == "critical":
                send_alert(
                    severity="CRITICAL",
                    message=f"{server['server_name']} uptime dropped "
                            f"{server['decline_percentage']:.1f}%"
                )
```

### SLA Reporting

```python
async def monthly_sla_report():
    # Compare this month vs last month
    import datetime
    now = datetime.datetime.now()

    # Calculate period boundaries
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0)
    last_month_end = this_month_start - datetime.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    # Compare periods
    result = await trends.compare_time_periods(
        period1_start=last_month_start.isoformat(),
        period1_end=last_month_end.isoformat(),
        period2_start=this_month_start.isoformat(),
        period2_end=now.isoformat()
    )

    return result
```

## Best Practices

1. **Run Regular Health Checks**
   - Schedule every 15-30 minutes
   - Always save to history with `--save-history`

2. **Set Appropriate Thresholds**
   - Start with 20% degradation threshold
   - Adjust based on your SLA requirements

3. **Review Trends Weekly**
   - Check `analyze_health_trends` for 7d window
   - Look for degradation_score < -1.0

4. **Monitor Critical Servers**
   - Use `get_server_history` for important services
   - Alert on uptime < 95%

5. **Clean Up Old Data**
   - Use `cleanup_old_diagnostics()` monthly
   - Retain 30-90 days based on compliance needs

## Troubleshooting

### Trend Analysis Returns No Data

**Problem:** `analyze_health_trends` returns `no_data` error.

**Solutions:**
1. Check if health checks are running: `diagnostic-mcp --check health`
2. Verify Supabase connection: Check `SUPABASE_URL` and `SUPABASE_KEY`
3. Run health check with history: `diagnostic-mcp --check health --save-history`
4. Query database directly: `SELECT COUNT(*) FROM diagnostic_history;`

### Degradation Score Always Zero

**Problem:** `degradation_score` is always 0.0.

**Solutions:**
1. Need at least 5 records for trend calculation
2. Check if uptime is constant (no variation = no trend)
3. Verify time window has enough data: Try `7d` instead of `1h`

### Response Time Stats Empty

**Problem:** `response_time` returns all zeros.

**Solutions:**
1. Servers must be online to record response times
2. Check if `online_servers` have `response_time_ms` field
3. Verify health checks are completing successfully

## API Reference

See the main README.md for complete API reference for:
- MCP tool schemas
- HTTP endpoint documentation
- Error code reference
- Response envelope format

## Related Documentation

- [README.md](../README.md) - Main diagnostic-mcp documentation
- [HEALTH_PROBES.md](../HEALTH_PROBES.md) - Health probe configuration
- [PHASE3_README.md](../PHASE3_README.md) - Historical tracking implementation
- [schema.sql](../schema.sql) - Database schema for diagnostic_history table
