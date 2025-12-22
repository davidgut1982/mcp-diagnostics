"""
Tests for diagnostic_mcp.trends module

Tests trend analysis, degradation detection, and period comparison functionality.
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
from diagnostic_mcp import trends


@pytest.fixture
def mock_supabase():
    """Mock Supabase client."""
    mock = Mock()
    return mock


@pytest.fixture
def sample_diagnostic_records():
    """Sample diagnostic records for testing."""
    base_time = datetime.now() - timedelta(hours=24)

    records = []
    for i in range(10):
        timestamp = base_time + timedelta(hours=i * 2)
        # Simulate degrading uptime
        servers_online = 10 - i  # Starts at 10, ends at 1
        servers_offline = i      # Starts at 0, ends at 9

        record = {
            "id": f"record_{i}",
            "created_at": timestamp.isoformat(),
            "status": "degraded" if i > 5 else "healthy",
            "servers_total": 10,
            "servers_online": servers_online,
            "servers_offline": servers_offline,
            "health_check_result": {
                "ok": True,
                "data": {
                    "total_checked": 10,
                    "servers_online": servers_online,
                    "servers_offline": servers_offline,
                    "online_servers": [
                        {
                            "name": f"server-{j}",
                            "status": "online",
                            "response_time_ms": 100 + (i * 10) + (j * 5),
                            "transport": "stdio"
                        }
                        for j in range(servers_online)
                    ],
                    "offline_servers": [
                        {
                            "name": f"server-{j}",
                            "status": "offline",
                            "transport": "stdio",
                            "error": "connection_refused"
                        }
                        for j in range(servers_online, 10)
                    ]
                }
            }
        }
        records.append(record)

    return records


class TestParseTimeWindow:
    """Test parse_time_window function."""

    def test_parse_hours(self):
        """Test parsing hour-based windows."""
        assert trends.parse_time_window("1h") == timedelta(hours=1)
        assert trends.parse_time_window("24h") == timedelta(hours=24)
        assert trends.parse_time_window("48h") == timedelta(hours=48)

    def test_parse_days(self):
        """Test parsing day-based windows."""
        assert trends.parse_time_window("1d") == timedelta(days=1)
        assert trends.parse_time_window("7d") == timedelta(days=7)
        assert trends.parse_time_window("30d") == timedelta(days=30)

    def test_invalid_format(self):
        """Test invalid time window format."""
        with pytest.raises(ValueError):
            trends.parse_time_window("24hours")

        with pytest.raises(ValueError):
            trends.parse_time_window("invalid")

        with pytest.raises(ValueError):
            trends.parse_time_window("")


class TestCalculateUptimePercentage:
    """Test calculate_uptime_percentage function."""

    def test_empty_records(self):
        """Test with no records."""
        assert trends.calculate_uptime_percentage([]) == 0.0

    def test_all_online(self, sample_diagnostic_records):
        """Test with all servers online."""
        # Take only first record (all online)
        records = sample_diagnostic_records[:1]
        uptime = trends.calculate_uptime_percentage(records)
        assert uptime == 100.0

    def test_all_offline(self, sample_diagnostic_records):
        """Test with all servers offline."""
        # Take only last record (all offline)
        records = sample_diagnostic_records[-1:]
        uptime = trends.calculate_uptime_percentage(records)
        # Last record has 1 online, 9 offline = 10% uptime
        assert uptime == 10.0

    def test_mixed_status(self, sample_diagnostic_records):
        """Test with mixed online/offline status."""
        uptime = trends.calculate_uptime_percentage(sample_diagnostic_records)
        # Total: 10 servers * 10 records = 100 checks
        # Online: 10+9+8+7+6+5+4+3+2+1 = 55
        # Uptime: 55/100 = 55%
        assert abs(uptime - 55.0) < 0.01  # Allow for floating point precision


class TestCalculateFailureRate:
    """Test calculate_failure_rate function."""

    def test_empty_records(self):
        """Test with no records."""
        assert trends.calculate_failure_rate([]) == 0.0

    def test_no_errors(self, sample_diagnostic_records):
        """Test with no server errors."""
        # Modify records to have no errors
        for record in sample_diagnostic_records:
            record["health_check_result"]["data"]["servers_error"] = 0

        failure_rate = trends.calculate_failure_rate(sample_diagnostic_records)
        assert failure_rate == 0.0


class TestCalculateResponseTimeStats:
    """Test calculate_response_time_stats function."""

    def test_empty_records(self):
        """Test with no records."""
        stats = trends.calculate_response_time_stats([])
        assert stats["mean"] == 0.0
        assert stats["count"] == 0

    def test_with_response_times(self, sample_diagnostic_records):
        """Test with records containing response times."""
        stats = trends.calculate_response_time_stats(sample_diagnostic_records)

        # Should have statistics
        assert stats["mean"] > 0
        assert stats["p50"] > 0
        assert stats["p95"] > 0
        assert stats["p99"] > 0
        assert stats["count"] > 0


class TestCountStatusChanges:
    """Test count_status_changes function."""

    def test_empty_records(self):
        """Test with no records."""
        changes = trends.count_status_changes([])
        assert changes["total_transitions"] == 0

    def test_single_record(self, sample_diagnostic_records):
        """Test with single record (no transitions possible)."""
        changes = trends.count_status_changes(sample_diagnostic_records[:1])
        assert changes["total_transitions"] == 0

    def test_transitions(self, sample_diagnostic_records):
        """Test counting transitions."""
        changes = trends.count_status_changes(sample_diagnostic_records)

        # Each server should have transitions as it goes offline
        assert changes["online_to_offline"] > 0
        assert changes["total_transitions"] > 0


class TestCalculateDegradationScore:
    """Test calculate_degradation_score function."""

    def test_insufficient_data(self):
        """Test with insufficient records."""
        score, trend = trends.calculate_degradation_score([])
        assert score == 0.0
        assert trend == "insufficient_data"

    def test_improving_trend(self):
        """Test with improving uptime."""
        # Create records with increasing uptime
        records = []
        for i in range(10):
            records.append({
                "health_check_result": {
                    "data": {
                        "total_checked": 10,
                        "servers_online": i + 1,  # Increasing
                        "servers_offline": 10 - (i + 1)
                    }
                }
            })

        score, trend = trends.calculate_degradation_score(records)
        assert score > 0  # Positive slope
        assert trend == "improving"

    def test_degrading_trend(self, sample_diagnostic_records):
        """Test with degrading uptime."""
        score, trend = trends.calculate_degradation_score(sample_diagnostic_records)
        assert score < 0  # Negative slope
        assert trend == "degrading"

    def test_stable_trend(self):
        """Test with stable uptime."""
        # Create records with constant uptime
        records = []
        for i in range(10):
            records.append({
                "health_check_result": {
                    "data": {
                        "total_checked": 10,
                        "servers_online": 8,  # Constant
                        "servers_offline": 2
                    }
                }
            })

        score, trend = trends.calculate_degradation_score(records)
        assert trend == "stable"


@pytest.mark.asyncio
class TestAnalyzeHealthTrends:
    """Test analyze_health_trends function."""

    async def test_no_data(self, mock_supabase):
        """Test with no historical data."""
        trends.supabase = mock_supabase
        mock_supabase.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value.data = []

        result = await trends.analyze_health_trends(time_window="24h")

        assert result["ok"] is False
        assert result["error"] == "no_data"

    async def test_with_data(self, mock_supabase, sample_diagnostic_records):
        """Test with historical data."""
        trends.supabase = mock_supabase
        mock_supabase.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value.data = sample_diagnostic_records

        result = await trends.analyze_health_trends(time_window="24h")

        assert result["ok"] is True
        assert "metrics" in result["data"]
        assert "uptime_percentage" in result["data"]["metrics"]
        assert "failure_rate" in result["data"]["metrics"]
        assert "response_time" in result["data"]["metrics"]


@pytest.mark.asyncio
class TestGetServerHistory:
    """Test get_server_history function."""

    async def test_no_data(self, mock_supabase):
        """Test with no server data."""
        trends.supabase = mock_supabase
        mock_supabase.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value.data = []

        result = await trends.get_server_history(
            server_name="test-server",
            time_window="24h"
        )

        assert result["ok"] is False
        assert result["error"] == "no_data"

    async def test_with_server_data(self, mock_supabase, sample_diagnostic_records):
        """Test with server history data."""
        trends.supabase = mock_supabase
        mock_supabase.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value.data = sample_diagnostic_records

        result = await trends.get_server_history(
            server_name="server-0",
            time_window="24h"
        )

        assert result["ok"] is True
        assert "server_name" in result["data"]
        assert result["data"]["server_name"] == "server-0"
        assert "total_checks" in result["data"]
        assert "uptime_percentage" in result["data"]


@pytest.mark.asyncio
class TestDetectDegradations:
    """Test detect_degradations function."""

    async def test_no_data(self, mock_supabase):
        """Test with no data."""
        trends.supabase = mock_supabase
        mock_supabase.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value.data = []

        result = await trends.detect_degradations(
            time_window="24h",
            threshold=20.0
        )

        assert result["ok"] is False
        assert result["error"] == "no_data"

    async def test_insufficient_data(self, mock_supabase):
        """Test with insufficient data."""
        trends.supabase = mock_supabase
        # Only 2 records (need at least 4)
        mock_supabase.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value.data = [
            {"id": "1"},
            {"id": "2"}
        ]

        result = await trends.detect_degradations(
            time_window="24h",
            threshold=20.0
        )

        assert result["ok"] is False
        assert result["error"] == "insufficient_data"

    async def test_detect_degradations(self, mock_supabase, sample_diagnostic_records):
        """Test degradation detection."""
        trends.supabase = mock_supabase
        mock_supabase.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value.data = sample_diagnostic_records

        result = await trends.detect_degradations(
            time_window="24h",
            threshold=20.0
        )

        assert result["ok"] is True
        assert "degraded_servers" in result["data"]
        # Should detect degradations (uptime drops from 100% to 10%)
        assert len(result["data"]["degraded_servers"]) > 0


@pytest.mark.asyncio
class TestCompareTimePeriods:
    """Test compare_time_periods function."""

    async def test_no_supabase(self):
        """Test with Supabase not initialized."""
        trends.supabase = None

        result = await trends.compare_time_periods(
            period1_start="2025-01-01T00:00:00Z",
            period1_end="2025-01-02T00:00:00Z",
            period2_start="2025-01-03T00:00:00Z",
            period2_end="2025-01-04T00:00:00Z"
        )

        assert result["ok"] is False
        assert result["error"] == "supabase_not_initialized"

    async def test_insufficient_data(self, mock_supabase):
        """Test with data in only one period."""
        trends.supabase = mock_supabase

        # Mock period 1 with data, period 2 empty
        def mock_execute():
            mock = Mock()
            # First call (period 1)
            if not hasattr(mock_execute, 'calls'):
                mock_execute.calls = 0
            mock_execute.calls += 1

            if mock_execute.calls == 1:
                mock.data = [{"id": "1"}]
            else:
                mock.data = []
            return mock

        mock_supabase.table.return_value.select.return_value.gte.return_value.lte.return_value.order.return_value.execute = mock_execute

        result = await trends.compare_time_periods(
            period1_start="2025-01-01T00:00:00Z",
            period1_end="2025-01-02T00:00:00Z",
            period2_start="2025-01-03T00:00:00Z",
            period2_end="2025-01-04T00:00:00Z"
        )

        assert result["ok"] is False
        assert result["error"] == "insufficient_data"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
