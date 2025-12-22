"""
Diagnostic Trends Analysis Module

Analyzes historical diagnostic data to identify trends, degradations,
and improvements in MCP server health over time.

Usage:
    from diagnostic_mcp.trends import analyze_health_trends, detect_degradations

    trends = await analyze_health_trends(time_window='24h')
    degradations = await detect_degradations(time_window='7d', threshold=20.0)
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict
import statistics

from supabase import Client

logger = logging.getLogger(__name__)

# Global Supabase client (initialized by server.py)
supabase: Optional[Client] = None


def initialize_supabase(client: Client):
    """Initialize Supabase client for trends analysis."""
    global supabase
    supabase = client
    logger.info("Initialized Supabase client for trends analysis")


def parse_time_window(window: str) -> timedelta:
    """
    Parse time window string to timedelta.

    Args:
        window: Time window string (e.g., '1h', '24h', '7d', '30d')

    Returns:
        timedelta object

    Raises:
        ValueError: If window format is invalid
    """
    if not window:
        raise ValueError("Time window cannot be empty")

    # Extract number and unit
    if window[-1] == 'h':
        hours = int(window[:-1])
        return timedelta(hours=hours)
    elif window[-1] == 'd':
        days = int(window[:-1])
        return timedelta(days=days)
    else:
        raise ValueError(f"Invalid time window format: {window}. Use '1h', '24h', '7d', '30d'")


async def get_historical_data(
    time_window: str,
    server_filter: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Query historical diagnostic data from Supabase.

    Args:
        time_window: Time window string (e.g., '24h', '7d')
        server_filter: Optional server name to filter by

    Returns:
        List of diagnostic records
    """
    if not supabase:
        logger.warning("Supabase not initialized, cannot query historical data")
        return []

    try:
        # Parse time window
        delta = parse_time_window(time_window)
        since = datetime.now() - delta

        # Query diagnostic_history table
        query = supabase.table("diagnostic_history")\
            .select("*")\
            .gte("created_at", since.isoformat())\
            .order("created_at", desc=False)

        response = query.execute()

        records = response.data if response.data else []

        # Filter by server if requested
        if server_filter:
            # Filter records that have health_check_result containing this server
            filtered_records = []
            for record in records:
                health_check = record.get("health_check_result", {})
                if not health_check:
                    continue

                health_data = health_check.get("data", {})
                online_servers = health_data.get("online_servers", [])
                offline_servers = health_data.get("offline_servers", [])

                # Check if server is mentioned in either list
                all_servers = online_servers + offline_servers
                if any(s.get("name") == server_filter for s in all_servers):
                    filtered_records.append(record)

            records = filtered_records

        logger.info(f"Retrieved {len(records)} historical records (window: {time_window})")
        return records

    except Exception as e:
        logger.error(f"Failed to query historical data: {e}", exc_info=True)
        return []


def calculate_uptime_percentage(records: List[Dict[str, Any]]) -> float:
    """
    Calculate overall uptime percentage from records.

    Args:
        records: List of diagnostic records

    Returns:
        Uptime percentage (0-100)
    """
    if not records:
        return 0.0

    total_checks = 0
    online_checks = 0

    for record in records:
        health_check = record.get("health_check_result", {})
        if not health_check:
            continue

        health_data = health_check.get("data", {})
        total_checked = health_data.get("total_checked", 0)
        servers_online = health_data.get("servers_online", 0)

        total_checks += total_checked
        online_checks += servers_online

    if total_checks == 0:
        return 0.0

    return (online_checks / total_checks) * 100


def calculate_failure_rate(records: List[Dict[str, Any]]) -> float:
    """
    Calculate failure rate from records.

    Args:
        records: List of diagnostic records

    Returns:
        Failure rate percentage (0-100)
    """
    if not records:
        return 0.0

    total_checks = 0
    error_checks = 0

    for record in records:
        health_check = record.get("health_check_result", {})
        if not health_check:
            continue

        health_data = health_check.get("data", {})
        total_checked = health_data.get("total_checked", 0)
        servers_error = health_data.get("servers_error", 0)

        total_checks += total_checked
        error_checks += servers_error

    if total_checks == 0:
        return 0.0

    return (error_checks / total_checks) * 100


def calculate_response_time_stats(records: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Calculate response time statistics from records.

    Args:
        records: List of diagnostic records

    Returns:
        Dictionary with mean, p50, p95, p99 response times
    """
    response_times = []

    for record in records:
        health_check = record.get("health_check_result", {})
        if not health_check:
            continue

        health_data = health_check.get("data", {})
        online_servers = health_data.get("online_servers", [])

        for server in online_servers:
            rt = server.get("response_time_ms")
            if rt is not None and rt > 0:
                response_times.append(rt)

    if not response_times:
        return {
            "mean": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "count": 0
        }

    sorted_times = sorted(response_times)
    count = len(sorted_times)

    return {
        "mean": round(statistics.mean(sorted_times), 2),
        "p50": round(sorted_times[int(count * 0.5)], 2),
        "p95": round(sorted_times[int(count * 0.95)], 2) if count > 1 else sorted_times[0],
        "p99": round(sorted_times[int(count * 0.99)], 2) if count > 1 else sorted_times[0],
        "count": count
    }


def count_status_changes(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Count status transitions (online→offline, offline→online).

    Args:
        records: List of diagnostic records (ordered by time)

    Returns:
        Dictionary with transition counts
    """
    if len(records) < 2:
        return {
            "online_to_offline": 0,
            "offline_to_online": 0,
            "total_transitions": 0
        }

    # Track server states across records
    server_states = defaultdict(list)

    for record in records:
        health_check = record.get("health_check_result", {})
        if not health_check:
            continue

        health_data = health_check.get("data", {})
        online_servers = health_data.get("online_servers", [])
        offline_servers = health_data.get("offline_servers", [])

        # Record online servers
        for server in online_servers:
            server_name = server.get("name")
            if server_name:
                server_states[server_name].append("online")

        # Record offline servers
        for server in offline_servers:
            server_name = server.get("name")
            if server_name:
                server_states[server_name].append("offline")

    # Count transitions
    online_to_offline = 0
    offline_to_online = 0

    for server_name, states in server_states.items():
        for i in range(1, len(states)):
            prev_state = states[i - 1]
            curr_state = states[i]

            if prev_state == "online" and curr_state == "offline":
                online_to_offline += 1
            elif prev_state == "offline" and curr_state == "online":
                offline_to_online += 1

    return {
        "online_to_offline": online_to_offline,
        "offline_to_online": offline_to_online,
        "total_transitions": online_to_offline + offline_to_online
    }


def calculate_degradation_score(records: List[Dict[str, Any]]) -> Tuple[float, str]:
    """
    Calculate degradation score using linear regression of uptime over time.

    Args:
        records: List of diagnostic records (ordered by time)

    Returns:
        Tuple of (degradation_score, trend_direction)
        - degradation_score: Slope of uptime trend (negative = degrading)
        - trend_direction: 'improving', 'degrading', 'stable', or 'insufficient_data'
    """
    if len(records) < 5:
        return (0.0, "insufficient_data")

    # Extract uptime percentages over time
    uptime_series = []

    for record in records:
        health_check = record.get("health_check_result", {})
        if not health_check:
            continue

        health_data = health_check.get("data", {})
        total_checked = health_data.get("total_checked", 0)
        servers_online = health_data.get("servers_online", 0)

        if total_checked > 0:
            uptime_pct = (servers_online / total_checked) * 100
            uptime_series.append(uptime_pct)

    if len(uptime_series) < 5:
        return (0.0, "insufficient_data")

    # Simple linear regression (calculate slope)
    n = len(uptime_series)
    x_values = list(range(n))
    y_values = uptime_series

    x_mean = sum(x_values) / n
    y_mean = sum(y_values) / n

    numerator = sum((x_values[i] - x_mean) * (y_values[i] - y_mean) for i in range(n))
    denominator = sum((x_values[i] - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return (0.0, "stable")

    slope = numerator / denominator

    # Determine trend direction
    # Slope > 1: improving (uptime increasing)
    # Slope < -1: degrading (uptime decreasing)
    # Otherwise: stable
    if slope > 1.0:
        trend = "improving"
    elif slope < -1.0:
        trend = "degrading"
    else:
        trend = "stable"

    return (round(slope, 2), trend)


async def analyze_health_trends(
    time_window: str = "24h",
    server_filter: Optional[str] = None
) -> Dict[str, Any]:
    """
    Analyze health trends over specified time window.

    Args:
        time_window: Time window string (e.g., '1h', '24h', '7d', '30d')
        server_filter: Optional server name to filter by

    Returns:
        Dictionary containing trend analysis results
    """
    try:
        # Get historical data
        records = await get_historical_data(time_window, server_filter)

        if not records:
            return {
                "ok": False,
                "error": "no_data",
                "message": f"No historical data found for time window: {time_window}",
                "data": {
                    "time_window": time_window,
                    "server_filter": server_filter,
                    "total_records": 0
                }
            }

        # Calculate metrics
        uptime_pct = calculate_uptime_percentage(records)
        failure_rate = calculate_failure_rate(records)
        response_time_stats = calculate_response_time_stats(records)
        status_changes = count_status_changes(records)
        degradation_score, trend_direction = calculate_degradation_score(records)

        # Build result
        result = {
            "ok": True,
            "message": f"Analyzed {len(records)} records over {time_window}",
            "data": {
                "time_window": time_window,
                "server_filter": server_filter,
                "total_records": len(records),
                "metrics": {
                    "uptime_percentage": round(uptime_pct, 2),
                    "failure_rate": round(failure_rate, 2),
                    "response_time": response_time_stats,
                    "status_changes": status_changes,
                    "degradation_score": degradation_score,
                    "trend_direction": trend_direction
                },
                "first_record": records[0].get("created_at") if records else None,
                "last_record": records[-1].get("created_at") if records else None
            }
        }

        logger.info(
            f"Trend analysis complete: {len(records)} records, "
            f"uptime={uptime_pct:.2f}%, trend={trend_direction}"
        )

        return result

    except Exception as e:
        logger.error(f"Failed to analyze health trends: {e}", exc_info=True)
        return {
            "ok": False,
            "error": "analysis_failed",
            "message": f"Failed to analyze health trends: {str(e)}",
            "data": None
        }


async def get_server_history(
    server_name: str,
    time_window: str = "24h"
) -> Dict[str, Any]:
    """
    Get historical health checks for a specific server.

    Args:
        server_name: Name of the server
        time_window: Time window string (e.g., '24h', '7d')

    Returns:
        Dictionary containing server history
    """
    try:
        # Get historical data filtered by server
        records = await get_historical_data(time_window, server_filter=server_name)

        if not records:
            return {
                "ok": False,
                "error": "no_data",
                "message": f"No historical data found for server '{server_name}' in {time_window}",
                "data": {
                    "server_name": server_name,
                    "time_window": time_window,
                    "total_records": 0
                }
            }

        # Extract server-specific data from each record
        server_history = []

        for record in records:
            health_check = record.get("health_check_result", {})
            if not health_check:
                continue

            health_data = health_check.get("data", {})
            online_servers = health_data.get("online_servers", [])
            offline_servers = health_data.get("offline_servers", [])

            # Find this server in the results
            server_data = None
            status = "unknown"

            for server in online_servers:
                if server.get("name") == server_name:
                    server_data = server
                    status = "online"
                    break

            if not server_data:
                for server in offline_servers:
                    if server.get("name") == server_name:
                        server_data = server
                        status = "offline"
                        break

            if server_data:
                server_history.append({
                    "timestamp": record.get("created_at"),
                    "status": status,
                    "response_time_ms": server_data.get("response_time_ms"),
                    "transport": server_data.get("transport"),
                    "error": server_data.get("error"),
                    "note": server_data.get("note")
                })

        # Calculate server-specific metrics
        total_checks = len(server_history)
        online_checks = sum(1 for h in server_history if h["status"] == "online")
        uptime_pct = (online_checks / total_checks * 100) if total_checks > 0 else 0

        # Response time stats (only for online checks)
        response_times = [h["response_time_ms"] for h in server_history
                         if h["status"] == "online" and h["response_time_ms"] is not None]

        response_stats = {}
        if response_times:
            sorted_times = sorted(response_times)
            count = len(sorted_times)
            response_stats = {
                "mean": round(statistics.mean(sorted_times), 2),
                "p50": round(sorted_times[int(count * 0.5)], 2),
                "p95": round(sorted_times[int(count * 0.95)], 2) if count > 1 else sorted_times[0],
                "p99": round(sorted_times[int(count * 0.99)], 2) if count > 1 else sorted_times[0],
                "count": count
            }

        result = {
            "ok": True,
            "message": f"Retrieved {total_checks} health checks for {server_name}",
            "data": {
                "server_name": server_name,
                "time_window": time_window,
                "total_checks": total_checks,
                "uptime_percentage": round(uptime_pct, 2),
                "response_time_stats": response_stats,
                "history": server_history,
                "first_check": server_history[0]["timestamp"] if server_history else None,
                "last_check": server_history[-1]["timestamp"] if server_history else None
            }
        }

        logger.info(
            f"Server history complete: {server_name}, {total_checks} checks, "
            f"uptime={uptime_pct:.2f}%"
        )

        return result

    except Exception as e:
        logger.error(f"Failed to get server history: {e}", exc_info=True)
        return {
            "ok": False,
            "error": "history_failed",
            "message": f"Failed to get server history: {str(e)}",
            "data": None
        }


async def detect_degradations(
    time_window: str = "24h",
    threshold: float = 20.0
) -> Dict[str, Any]:
    """
    Detect servers with declining uptime (degradations).

    Args:
        time_window: Time window string (e.g., '24h', '7d')
        threshold: Minimum uptime decline percentage to flag (default: 20.0)

    Returns:
        Dictionary containing degradation analysis
    """
    try:
        # Get historical data
        records = await get_historical_data(time_window)

        if not records:
            return {
                "ok": False,
                "error": "no_data",
                "message": f"No historical data found for time window: {time_window}",
                "data": {
                    "time_window": time_window,
                    "threshold": threshold,
                    "total_records": 0
                }
            }

        # Split records into first half and second half
        midpoint = len(records) // 2
        first_half = records[:midpoint]
        second_half = records[midpoint:]

        if len(first_half) < 2 or len(second_half) < 2:
            return {
                "ok": False,
                "error": "insufficient_data",
                "message": "Insufficient data to detect degradations (need at least 4 records)",
                "data": {
                    "time_window": time_window,
                    "threshold": threshold,
                    "total_records": len(records)
                }
            }

        # Extract all unique server names
        all_servers = set()
        for record in records:
            health_check = record.get("health_check_result", {})
            if not health_check:
                continue

            health_data = health_check.get("data", {})
            online_servers = health_data.get("online_servers", [])
            offline_servers = health_data.get("offline_servers", [])

            for server in online_servers + offline_servers:
                server_name = server.get("name")
                if server_name:
                    all_servers.add(server_name)

        # Calculate uptime for each server in each half
        degraded_servers = []

        for server_name in all_servers:
            # First half uptime
            first_half_online = 0
            first_half_total = 0

            for record in first_half:
                health_check = record.get("health_check_result", {})
                if not health_check:
                    continue

                health_data = health_check.get("data", {})
                online_servers = health_data.get("online_servers", [])
                offline_servers = health_data.get("offline_servers", [])

                for server in online_servers:
                    if server.get("name") == server_name:
                        first_half_online += 1
                        first_half_total += 1
                        break
                else:
                    for server in offline_servers:
                        if server.get("name") == server_name:
                            first_half_total += 1
                            break

            # Second half uptime
            second_half_online = 0
            second_half_total = 0

            for record in second_half:
                health_check = record.get("health_check_result", {})
                if not health_check:
                    continue

                health_data = health_check.get("data", {})
                online_servers = health_data.get("online_servers", [])
                offline_servers = health_data.get("offline_servers", [])

                for server in online_servers:
                    if server.get("name") == server_name:
                        second_half_online += 1
                        second_half_total += 1
                        break
                else:
                    for server in offline_servers:
                        if server.get("name") == server_name:
                            second_half_total += 1
                            break

            # Calculate uptime percentages
            first_uptime = (first_half_online / first_half_total * 100) if first_half_total > 0 else 0
            second_uptime = (second_half_online / second_half_total * 100) if second_half_total > 0 else 0

            # Check for degradation
            decline = first_uptime - second_uptime

            if decline >= threshold:
                degraded_servers.append({
                    "server_name": server_name,
                    "first_period_uptime": round(first_uptime, 2),
                    "second_period_uptime": round(second_uptime, 2),
                    "decline_percentage": round(decline, 2),
                    "severity": "critical" if decline >= 50 else "warning"
                })

        # Sort by decline percentage (worst first)
        degraded_servers.sort(key=lambda s: s["decline_percentage"], reverse=True)

        result = {
            "ok": True,
            "message": f"Detected {len(degraded_servers)} degraded servers in {time_window}",
            "data": {
                "time_window": time_window,
                "threshold": threshold,
                "total_servers_analyzed": len(all_servers),
                "degraded_servers_count": len(degraded_servers),
                "degraded_servers": degraded_servers,
                "period_split": {
                    "first_period_records": len(first_half),
                    "second_period_records": len(second_half)
                }
            }
        }

        logger.info(
            f"Degradation detection complete: {len(degraded_servers)} degraded servers "
            f"(threshold={threshold}%)"
        )

        return result

    except Exception as e:
        logger.error(f"Failed to detect degradations: {e}", exc_info=True)
        return {
            "ok": False,
            "error": "detection_failed",
            "message": f"Failed to detect degradations: {str(e)}",
            "data": None
        }


async def compare_time_periods(
    period1_start: str,
    period1_end: str,
    period2_start: str,
    period2_end: str
) -> Dict[str, Any]:
    """
    Compare metrics between two time periods.

    Args:
        period1_start: ISO timestamp for period 1 start
        period1_end: ISO timestamp for period 1 end
        period2_start: ISO timestamp for period 2 start
        period2_end: ISO timestamp for period 2 end

    Returns:
        Dictionary containing comparison results
    """
    if not supabase:
        logger.warning("Supabase not initialized, cannot compare time periods")
        return {
            "ok": False,
            "error": "supabase_not_initialized",
            "message": "Supabase client not initialized",
            "data": None
        }

    try:
        # Query period 1
        period1_query = supabase.table("diagnostic_history")\
            .select("*")\
            .gte("created_at", period1_start)\
            .lte("created_at", period1_end)\
            .order("created_at", desc=False)

        period1_response = period1_query.execute()
        period1_records = period1_response.data if period1_response.data else []

        # Query period 2
        period2_query = supabase.table("diagnostic_history")\
            .select("*")\
            .gte("created_at", period2_start)\
            .lte("created_at", period2_end)\
            .order("created_at", desc=False)

        period2_response = period2_query.execute()
        period2_records = period2_response.data if period2_response.data else []

        if not period1_records or not period2_records:
            return {
                "ok": False,
                "error": "insufficient_data",
                "message": "Insufficient data in one or both periods",
                "data": {
                    "period1_records": len(period1_records),
                    "period2_records": len(period2_records)
                }
            }

        # Calculate metrics for both periods
        period1_uptime = calculate_uptime_percentage(period1_records)
        period2_uptime = calculate_uptime_percentage(period2_records)

        period1_failure_rate = calculate_failure_rate(period1_records)
        period2_failure_rate = calculate_failure_rate(period2_records)

        period1_response_time = calculate_response_time_stats(period1_records)
        period2_response_time = calculate_response_time_stats(period2_records)

        period1_changes = count_status_changes(period1_records)
        period2_changes = count_status_changes(period2_records)

        # Calculate deltas
        uptime_delta = period2_uptime - period1_uptime
        failure_rate_delta = period2_failure_rate - period1_failure_rate
        response_time_delta = period2_response_time["mean"] - period1_response_time["mean"]

        # Determine overall trend
        if uptime_delta > 5:
            overall_trend = "improving"
        elif uptime_delta < -5:
            overall_trend = "degrading"
        else:
            overall_trend = "stable"

        result = {
            "ok": True,
            "message": "Period comparison completed",
            "data": {
                "period1": {
                    "start": period1_start,
                    "end": period1_end,
                    "records": len(period1_records),
                    "uptime_percentage": round(period1_uptime, 2),
                    "failure_rate": round(period1_failure_rate, 2),
                    "response_time": period1_response_time,
                    "status_changes": period1_changes
                },
                "period2": {
                    "start": period2_start,
                    "end": period2_end,
                    "records": len(period2_records),
                    "uptime_percentage": round(period2_uptime, 2),
                    "failure_rate": round(period2_failure_rate, 2),
                    "response_time": period2_response_time,
                    "status_changes": period2_changes
                },
                "comparison": {
                    "uptime_delta": round(uptime_delta, 2),
                    "failure_rate_delta": round(failure_rate_delta, 2),
                    "response_time_delta": round(response_time_delta, 2),
                    "overall_trend": overall_trend
                }
            }
        }

        logger.info(
            f"Period comparison complete: uptime delta={uptime_delta:.2f}%, "
            f"trend={overall_trend}"
        )

        return result

    except Exception as e:
        logger.error(f"Failed to compare time periods: {e}", exc_info=True)
        return {
            "ok": False,
            "error": "comparison_failed",
            "message": f"Failed to compare time periods: {str(e)}",
            "data": None
        }
