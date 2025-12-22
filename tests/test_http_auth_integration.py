#!/usr/bin/env python3
"""
Integration tests for HTTP server authentication.

Tests the full authentication flow via HTTP endpoints.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from starlette.testclient import TestClient
from diagnostic_mcp.auth import AuthManager, MemoryTokenStorage


@pytest.fixture
def auth_manager():
    """Create auth manager for testing."""
    storage = MemoryTokenStorage()
    return AuthManager(
        storage=storage,
        admin_token="test-admin-token",
        default_ttl_hours=24
    )


@pytest.fixture
def http_server(auth_manager):
    """Create test HTTP server with auth enabled."""
    # Import here to avoid circular dependencies
    import http_server
    from diagnostic_mcp import server as diagnostic_server

    # Mock MCP server
    mock_mcp_server = Mock()
    mock_mcp_server.list_tools = Mock(return_value=[])

    # Mock health monitor
    mock_health_monitor = Mock()
    mock_health_monitor.allowed_rejections = 100
    mock_health_monitor.sampling_interval.total_seconds = Mock(return_value=10)
    mock_health_monitor.recovery_interval.total_seconds = Mock(return_value=20)
    mock_health_monitor.startup_duration.total_seconds = Mock(return_value=30)
    mock_health_monitor.degraded_threshold = 0.25
    mock_health_monitor.record_request = Mock()

    # Set auth manager
    diagnostic_server.set_auth_manager(auth_manager)

    # Create app
    app = http_server.create_app(mock_mcp_server, mock_health_monitor, auth_manager)

    return TestClient(app)


class TestHTTPAuthIntegration:
    """Integration tests for HTTP authentication."""

    def test_health_endpoint_public(self, http_server):
        """Test that health endpoints are public (no auth required)."""
        response = http_server.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "UP"

    def test_info_endpoint_public(self, http_server):
        """Test that info endpoint is public (no auth required)."""
        response = http_server.get("/info")

        assert response.status_code == 200
        assert "diagnostic-mcp" in response.json()["name"]

    def test_protected_endpoint_requires_auth(self, http_server):
        """Test that protected endpoints require authentication."""
        # Try to access diagnostics without auth
        response = http_server.get("/diagnostics")

        assert response.status_code == 401
        assert "Missing Authorization header" in response.json()["error"]

    def test_create_token_with_admin_token(self, http_server):
        """Test creating session token with admin token."""
        response = http_server.post(
            "/auth/token",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"ttl_hours": 48}
        )

        assert response.status_code == 201
        data = response.json()

        assert data["status"] == "success"
        assert "token" in data["data"]
        assert "token_id" in data["data"]
        assert data["data"]["ttl_hours"] == 48

    def test_create_token_without_admin_token(self, http_server):
        """Test that token creation requires admin token."""
        response = http_server.post(
            "/auth/token",
            headers={"Authorization": "Bearer wrong-token"}
        )

        assert response.status_code == 401
        assert "Invalid admin token" in response.json()["error"]

    def test_access_protected_endpoint_with_session_token(self, http_server):
        """Test accessing protected endpoint with valid session token."""
        # First, create a session token
        create_response = http_server.post(
            "/auth/token",
            headers={"Authorization": "Bearer test-admin-token"}
        )

        assert create_response.status_code == 201
        token = create_response.json()["data"]["token"]

        # Now use the session token to access protected endpoint
        # Note: diagnostics endpoint needs mocked dependencies
        # For this test, we'll just verify it gets past auth
        response = http_server.get(
            "/diagnostics",
            headers={"Authorization": f"Bearer {token}"}
        )

        # Should not return 401 (auth failure)
        # Might return 500 due to mock dependencies, but that's OK
        assert response.status_code != 401

    def test_invalid_token_rejected(self, http_server):
        """Test that invalid tokens are rejected."""
        response = http_server.get(
            "/diagnostics",
            headers={"Authorization": "Bearer invalid-token-12345"}
        )

        assert response.status_code == 401
        assert "Invalid or expired token" in response.json()["error"]

    def test_malformed_auth_header_rejected(self, http_server):
        """Test that malformed auth headers are rejected."""
        # Missing "Bearer " prefix
        response = http_server.get(
            "/diagnostics",
            headers={"Authorization": "token-without-bearer"}
        )

        assert response.status_code == 401
        assert "Invalid Authorization header format" in response.json()["error"]


class TestHTTPAuthRateLimiting:
    """Test rate limiting for token creation."""

    def test_rate_limiting_enforced(self, http_server):
        """Test that rate limiting is enforced for token creation."""
        # Create tokens until rate limit hit
        # Default rate limiter: 5 tokens per 60 seconds

        successful = 0
        for i in range(10):
            response = http_server.post(
                "/auth/token",
                headers={"Authorization": "Bearer test-admin-token"}
            )

            if response.status_code == 201:
                successful += 1
            elif response.status_code == 429:
                # Rate limit hit
                assert "Rate limit exceeded" in response.json()["error"]
                break

        # Should have successfully created 5 tokens before rate limit
        assert successful == 5


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
