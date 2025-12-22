#!/usr/bin/env python3
"""
Tests for HealthMonitor probe logic.

Tests:
- Startup probe transitions
- Readiness probe rejection tracking
- Liveness probe failure detection
- Degraded state detection
- Probe status metadata
- Configuration thresholds
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Add parent directory to path for imports
parent_path = Path(__file__).parent.parent
if str(parent_path) not in sys.path:
    sys.path.insert(0, str(parent_path))

from http_server import HealthMonitor


class TestHealthMonitor:
    """Test suite for HealthMonitor class."""

    def test_initialization(self):
        """Test HealthMonitor initialization with default values."""
        monitor = HealthMonitor()

        assert monitor.allowed_rejections == 100
        assert monitor.sampling_interval == timedelta(seconds=10)
        assert monitor.recovery_interval == timedelta(seconds=20)  # 2x sampling
        assert monitor.startup_duration == timedelta(seconds=30)
        assert monitor.degraded_threshold == 0.25
        assert monitor.is_live is True
        assert monitor.is_ready is False  # Starts unready until startup completes
        assert monitor.rejection_count == 0
        assert monitor.total_requests == 0
        assert monitor.failed_requests == 0

    def test_custom_configuration(self):
        """Test HealthMonitor with custom configuration."""
        monitor = HealthMonitor(
            allowed_rejections=50,
            sampling_interval_seconds=5,
            recovery_interval_seconds=15,
            startup_duration_seconds=10,
            degraded_threshold=0.5
        )

        assert monitor.allowed_rejections == 50
        assert monitor.sampling_interval == timedelta(seconds=5)
        assert monitor.recovery_interval == timedelta(seconds=15)
        assert monitor.startup_duration == timedelta(seconds=10)
        assert monitor.degraded_threshold == 0.5

    def test_startup_probe_initial_state(self):
        """Test startup probe returns DOWN initially."""
        monitor = HealthMonitor(startup_duration_seconds=10)

        status = monitor.get_startup_status()

        assert status["status"] == "DOWN"
        assert status["startup_complete"] is False
        assert "startup_remaining_seconds" in status
        assert status["uptime_seconds"] < 10

    def test_startup_probe_after_duration(self):
        """Test startup probe returns UP after startup duration."""
        # Use very short startup duration for testing
        monitor = HealthMonitor(startup_duration_seconds=0)

        status = monitor.get_startup_status()

        assert status["status"] == "UP"
        assert status["startup_complete"] is True
        assert "startup_remaining_seconds" not in status

    def test_liveness_probe_default_up(self):
        """Test liveness probe is UP by default."""
        monitor = HealthMonitor()

        status = monitor.get_liveness()

        assert status["status"] == "UP"
        assert status["consecutive_failures"] == 0

    def test_liveness_probe_critical_failure(self):
        """Test liveness probe goes DOWN after 10 consecutive failures."""
        monitor = HealthMonitor()

        # Record 10 consecutive failures
        for _ in range(10):
            monitor.record_request(success=False)

        status = monitor.get_liveness()

        assert status["status"] == "DOWN"
        assert status["consecutive_failures"] == 10
        assert status["reason"] == "critical_failure_threshold_exceeded"

    def test_liveness_probe_resets_on_success(self):
        """Test liveness probe failure counter resets on success."""
        monitor = HealthMonitor()

        # Record 5 failures
        for _ in range(5):
            monitor.record_request(success=False)

        # Record success
        monitor.record_request(success=True)

        status = monitor.get_liveness()

        assert status["status"] == "UP"
        assert status["consecutive_failures"] == 0

    def test_readiness_probe_starts_unready(self):
        """Test readiness probe starts unready during startup."""
        monitor = HealthMonitor(startup_duration_seconds=10)

        status = monitor.get_readiness()

        assert status["status"] == "DOWN"
        assert status["reason"] == "startup_incomplete"
        assert "startup_remaining_seconds" in status

    def test_readiness_probe_becomes_ready_after_startup(self):
        """Test readiness probe becomes ready after startup completes."""
        monitor = HealthMonitor(startup_duration_seconds=0)

        # Force check
        monitor._check_readiness(datetime.now())

        status = monitor.get_readiness()

        assert status["status"] == "UP"
        assert monitor.is_ready is True

    def test_readiness_probe_rejection_tracking(self):
        """Test readiness probe tracks rejections."""
        monitor = HealthMonitor(
            allowed_rejections=10,
            sampling_interval_seconds=1,
            startup_duration_seconds=0
        )

        # Mark ready first
        monitor._check_readiness(datetime.now())
        monitor.is_ready = True

        # Record rejections exceeding threshold
        for _ in range(15):
            monitor.record_request(success=False)

        # Force sampling interval reset
        import time
        time.sleep(1.1)
        monitor.record_request(success=False)

        assert monitor.is_ready is False

    def test_degraded_state_detection(self):
        """Test degraded state detection based on error rate."""
        monitor = HealthMonitor(
            degraded_threshold=0.25,
            startup_duration_seconds=0
        )

        # Mark ready
        monitor._check_readiness(datetime.now())
        monitor.is_ready = True

        # Record requests with 30% error rate (above 25% threshold)
        for _ in range(7):
            monitor.record_request(success=True)
        for _ in range(3):
            monitor.record_request(success=False)

        status = monitor.get_readiness()

        assert status["status"] == "UP"  # Still ready
        assert status["degraded"] is True  # But degraded
        assert status["metrics"]["error_rate"] == 0.3
        assert "message" in status

    def test_probe_status_comprehensive(self):
        """Test comprehensive probe status returns all probes."""
        monitor = HealthMonitor(startup_duration_seconds=0)

        # Mark ready
        monitor._check_readiness(datetime.now())

        status = monitor.get_probe_status()

        assert "overall_status" in status
        assert "timestamp" in status
        assert "probes" in status
        assert "summary" in status

        # Check probes structure
        probes = status["probes"]
        assert "startup" in probes
        assert "liveness" in probes
        assert "readiness" in probes

        # Check summary
        summary = status["summary"]
        assert "startup_complete" in summary
        assert "is_live" in summary
        assert "is_ready" in summary
        assert "is_degraded" in summary
        assert "uptime_seconds" in summary

    def test_overall_status_calculation(self):
        """Test overall status determination based on probe states."""
        # Healthy state
        monitor = HealthMonitor(startup_duration_seconds=0)
        monitor._check_readiness(datetime.now())
        status = monitor.get_probe_status()
        assert status["overall_status"] == "healthy"

        # Starting state
        monitor2 = HealthMonitor(startup_duration_seconds=10)
        status2 = monitor2.get_probe_status()
        assert status2["overall_status"] == "starting"

        # Degraded state
        monitor3 = HealthMonitor(startup_duration_seconds=0, degraded_threshold=0.25)
        monitor3._check_readiness(datetime.now())
        monitor3.is_ready = True
        for _ in range(7):
            monitor3.record_request(success=True)
        for _ in range(3):
            monitor3.record_request(success=False)
        status3 = monitor3.get_probe_status()
        assert status3["overall_status"] == "degraded"

        # Critical state (liveness down)
        monitor4 = HealthMonitor(startup_duration_seconds=0)
        for _ in range(10):
            monitor4.record_request(success=False)
        status4 = monitor4.get_probe_status()
        assert status4["overall_status"] == "critical"

    def test_recovery_after_unready(self):
        """Test recovery to ready state after recovery interval."""
        import time

        monitor = HealthMonitor(
            allowed_rejections=5,
            sampling_interval_seconds=1,
            recovery_interval_seconds=2,
            startup_duration_seconds=0
        )

        # Mark ready
        monitor._check_readiness(datetime.now())
        monitor.is_ready = True

        # Trigger unready state
        for _ in range(10):
            monitor.record_request(success=False)

        time.sleep(1.1)
        monitor.record_request(success=False)

        assert monitor.is_ready is False

        # Wait for recovery interval
        time.sleep(2.1)
        monitor._check_readiness(datetime.now())

        assert monitor.is_ready is True

    def test_metadata_completeness(self):
        """Test that all probe methods return complete metadata."""
        monitor = HealthMonitor(startup_duration_seconds=0)
        monitor._check_readiness(datetime.now())

        # Startup probe metadata
        startup = monitor.get_startup_status()
        assert "status" in startup
        assert "timestamp" in startup
        assert "uptime_seconds" in startup
        assert "startup_duration_seconds" in startup
        assert "startup_complete" in startup

        # Liveness probe metadata
        liveness = monitor.get_liveness()
        assert "status" in liveness
        assert "timestamp" in liveness
        assert "uptime_seconds" in liveness
        assert "last_health_check" in liveness
        assert "consecutive_failures" in liveness

        # Readiness probe metadata
        readiness = monitor.get_readiness()
        assert "status" in readiness
        assert "timestamp" in readiness
        assert "degraded" in readiness
        assert "metrics" in readiness
        assert "uptime_seconds" in readiness

        # Metrics structure
        metrics = readiness["metrics"]
        assert "total_requests" in metrics
        assert "failed_requests" in metrics
        assert "current_rejections" in metrics
        assert "rejection_threshold" in metrics
        assert "error_rate" in metrics
        assert "degraded_threshold" in metrics


def test_health_monitor_import():
    """Test that HealthMonitor can be imported from http_server."""
    from http_server import HealthMonitor

    monitor = HealthMonitor()
    assert monitor is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
