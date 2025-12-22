"""
Diagnostic History Tracking Module

Handles saving diagnostic results to Supabase for trend analysis,
monitoring, and historical comparison.

Usage:
    from diagnostic_mcp.history import save_diagnostic_run, get_latest_diagnostics

    # Save a diagnostic run
    await save_diagnostic_run(results, check_type='all', triggered_by='cli')

    # Get latest results
    latest = await get_latest_diagnostics(check_type='health')
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import time

from supabase import create_client, Client

logger = logging.getLogger(__name__)

# Global Supabase client (initialized by server.py or cli.py)
supabase: Optional[Client] = None


def initialize_supabase(url: str, key: str):
    """Initialize Supabase client for history tracking."""
    global supabase
    supabase = create_client(url, key)
    logger.info(f"Initialized Supabase client for history tracking: {url}")


async def save_diagnostic_run(
    results: Dict[str, Any],
    check_type: str = "all",
    triggered_by: str = "unknown",
    execution_time_ms: Optional[int] = None,
    timeout_seconds: int = 5
) -> Optional[str]:
    """
    Save a diagnostic run to Supabase history.

    Args:
        results: Dictionary containing check results (port_check, health_check, etc.)
        check_type: Type of check run ('all', 'health', 'ports', 'config', 'tools')
        triggered_by: How the diagnostic was triggered ('cli', 'http', 'scheduled')
        execution_time_ms: How long the diagnostic took in milliseconds
        timeout_seconds: Timeout used for health checks

    Returns:
        str: UUID of created record, or None if save failed
    """
    if not supabase:
        logger.warning("Supabase not initialized, cannot save diagnostic history")
        return None

    try:
        # Calculate summary statistics
        summary = _calculate_summary(results)

        # Build record
        record = {
            "created_at": datetime.now().isoformat(),
            "check_type": check_type,
            "triggered_by": triggered_by,
            "status": summary["status"],
            "total_issues": summary["total_issues"],
            "critical_issues": summary["critical_issues"],
            "execution_time_ms": execution_time_ms,
            "timeout_seconds": timeout_seconds,
        }

        # Add check results as JSONB
        if "port_check" in results:
            record["port_check_result"] = results["port_check"]
            record["port_conflicts"] = results["port_check"].get("data", {}).get("summary", {}).get("conflicts_count", 0)

        if "health_check" in results:
            record["health_check_result"] = results["health_check"]
            health_data = results["health_check"].get("data", {})
            record["servers_total"] = health_data.get("total_checked", 0)
            record["servers_online"] = health_data.get("servers_online", 0)
            record["servers_offline"] = health_data.get("servers_offline", 0)

            # Count servers with partial status (stdio fails, HTTP works)
            partial_count = 0
            dual_transport_count = 0
            venv_issue_count = 0

            for server in health_data.get("offline_servers", []):
                if server.get("status") == "partial":
                    partial_count += 1
                if server.get("alternative_transports"):
                    dual_transport_count += 1
                if server.get("venv_health", {}).get("status") in ["broken", "error"]:
                    venv_issue_count += 1

            record["servers_partial"] = partial_count
            record["detected_dual_transports"] = dual_transport_count
            record["venv_issues"] = venv_issue_count

        if "config_check" in results:
            record["config_check_result"] = results["config_check"]
            record["config_issues"] = results["config_check"].get("data", {}).get("servers_with_issues", 0)

        if "tool_check" in results:
            record["tool_check_result"] = results["tool_check"]
            record["tool_conflicts"] = len(results["tool_check"].get("data", {}).get("naming_conflicts", []))

        # Insert into Supabase
        response = supabase.table("diagnostic_history").insert(record).execute()

        if response.data and len(response.data) > 0:
            record_id = response.data[0].get("id")
            logger.info(f"Saved diagnostic history: {record_id} (status: {summary['status']})")
            return record_id
        else:
            logger.error(f"Failed to save diagnostic history: no data returned")
            return None

    except Exception as e:
        logger.error(f"Failed to save diagnostic history: {e}", exc_info=True)
        return None


def _calculate_summary(results: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate summary statistics from diagnostic results."""
    total_issues = 0
    critical_issues = 0
    offline_servers = 0

    # Count issues from each check
    for check_name, check_result in results.items():
        if not check_result.get("ok", True):
            total_issues += 1

    # Count critical issues (offline servers)
    if "health_check" in results:
        health_data = results["health_check"].get("data", {})
        offline_servers = health_data.get("servers_offline", 0)
        critical_issues += offline_servers

    # Determine overall status
    if critical_issues > 0:
        status = "critical"
    elif total_issues > 0:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "total_issues": total_issues,
        "critical_issues": critical_issues,
        "offline_servers": offline_servers
    }


async def get_latest_diagnostics(
    check_type: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Get the most recent diagnostic runs.

    Args:
        check_type: Filter by check type (None for all types)
        limit: Maximum number of records to return

    Returns:
        List of diagnostic records, newest first
    """
    if not supabase:
        logger.warning("Supabase not initialized, cannot query diagnostic history")
        return []

    try:
        query = supabase.table("diagnostic_history").select("*").order("created_at", desc=True).limit(limit)

        if check_type:
            query = query.eq("check_type", check_type)

        response = query.execute()

        if response.data:
            logger.info(f"Retrieved {len(response.data)} diagnostic records")
            return response.data
        else:
            return []

    except Exception as e:
        logger.error(f"Failed to query diagnostic history: {e}", exc_info=True)
        return []


async def get_diagnostic_trends(
    hours: int = 24,
    check_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get diagnostic trends for the specified time period.

    Args:
        hours: Number of hours to look back
        check_type: Filter by check type (None for all types)

    Returns:
        Dictionary containing trend statistics
    """
    if not supabase:
        logger.warning("Supabase not initialized, cannot query diagnostic trends")
        return {}

    try:
        since = datetime.now() - timedelta(hours=hours)

        query = supabase.table("diagnostic_history")\
            .select("*")\
            .gte("created_at", since.isoformat())\
            .order("created_at", desc=False)

        if check_type:
            query = query.eq("check_type", check_type)

        response = query.execute()

        if not response.data:
            return {
                "period_hours": hours,
                "total_runs": 0,
                "trends": {}
            }

        records = response.data

        # Calculate trends
        total_runs = len(records)
        statuses = [r["status"] for r in records]

        # Status distribution
        status_counts = {
            "healthy": statuses.count("healthy"),
            "degraded": statuses.count("degraded"),
            "critical": statuses.count("critical"),
            "error": statuses.count("error")
        }

        # Average metrics
        avg_metrics = {
            "servers_offline": sum(r.get("servers_offline", 0) for r in records) / total_runs if total_runs > 0 else 0,
            "servers_partial": sum(r.get("servers_partial", 0) for r in records) / total_runs if total_runs > 0 else 0,
            "critical_issues": sum(r.get("critical_issues", 0) for r in records) / total_runs if total_runs > 0 else 0,
            "detected_dual_transports": sum(r.get("detected_dual_transports", 0) for r in records) / total_runs if total_runs > 0 else 0,
        }

        # Recent status (last 5 runs)
        recent_statuses = [r["status"] for r in records[-5:]] if len(records) >= 5 else [r["status"] for r in records]

        return {
            "period_hours": hours,
            "total_runs": total_runs,
            "status_distribution": status_counts,
            "average_metrics": avg_metrics,
            "recent_statuses": recent_statuses,
            "trend": _determine_trend(records)
        }

    except Exception as e:
        logger.error(f"Failed to calculate diagnostic trends: {e}", exc_info=True)
        return {}


def _determine_trend(records: List[Dict[str, Any]]) -> str:
    """
    Determine if diagnostic results are improving, degrading, or stable.

    Args:
        records: List of diagnostic records ordered by time (oldest first)

    Returns:
        'improving', 'degrading', 'stable', or 'insufficient_data'
    """
    if len(records) < 5:
        return "insufficient_data"

    # Compare first half vs second half
    midpoint = len(records) // 2
    first_half = records[:midpoint]
    second_half = records[midpoint:]

    # Count critical issues in each half
    first_half_critical = sum(1 for r in first_half if r["status"] == "critical")
    second_half_critical = sum(1 for r in second_half if r["status"] == "critical")

    # Calculate average offline servers
    first_half_avg_offline = sum(r.get("servers_offline", 0) for r in first_half) / len(first_half)
    second_half_avg_offline = sum(r.get("servers_offline", 0) for r in second_half) / len(second_half)

    # Determine trend based on critical issues and offline servers
    if second_half_critical < first_half_critical and second_half_avg_offline < first_half_avg_offline:
        return "improving"
    elif second_half_critical > first_half_critical or second_half_avg_offline > first_half_avg_offline:
        return "degrading"
    else:
        return "stable"


async def cleanup_old_diagnostics(days_to_keep: int = 30) -> int:
    """
    Clean up diagnostic history older than specified days.

    Args:
        days_to_keep: Number of days of history to retain

    Returns:
        Number of records deleted
    """
    if not supabase:
        logger.warning("Supabase not initialized, cannot cleanup diagnostic history")
        return 0

    try:
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)

        response = supabase.table("diagnostic_history")\
            .delete()\
            .lt("created_at", cutoff_date.isoformat())\
            .execute()

        deleted_count = len(response.data) if response.data else 0
        logger.info(f"Cleaned up {deleted_count} diagnostic records older than {days_to_keep} days")
        return deleted_count

    except Exception as e:
        logger.error(f"Failed to cleanup diagnostic history: {e}", exc_info=True)
        return 0
