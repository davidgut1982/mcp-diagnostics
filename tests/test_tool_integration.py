#!/usr/bin/env python3
"""
Tests for tool integration checks.

Tests the new tool callability, namespace verification, and real invocation features.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from diagnostic_mcp.server import (
    handle_check_tool_callability,
    handle_check_namespace_verification,
    handle_check_real_invocation,
    handle_check_tool_integration,
)


class TestToolCallability:
    """Tests for check_tool_callability handler."""

    @pytest.mark.asyncio
    async def test_no_supabase_connection(self):
        """Test error when Supabase is not available."""
        with patch("diagnostic_mcp.server.supabase", None):
            result = await handle_check_tool_callability({})

            assert len(result) == 1
            import json
            response = json.loads(result[0].text)

            assert response["ok"] is False
            assert "Supabase connection not available" in response["message"]

    @pytest.mark.asyncio
    async def test_with_mock_supabase(self):
        """Test with mocked Supabase data."""
        # Mock Supabase
        mock_supabase = MagicMock()

        # Mock servers query
        mock_servers_result = MagicMock()
        mock_servers_result.data = [
            {"server_id": "knowledge-mcp", "status": "active", "last_indexed": "2025-01-01"},
            {"server_id": "github-mcp", "status": "active", "last_indexed": "2025-01-01"},
        ]

        # Mock tools query
        mock_tools_result = MagicMock()
        mock_tools_result.data = [
            {"server_id": "knowledge-mcp", "tool_name": "kb_search"},
            {"server_id": "knowledge-mcp", "tool_name": "kb_add"},
            {"server_id": "github-mcp", "tool_name": "github_user_get"},
        ]

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_servers_result
        mock_supabase.table.return_value.select.return_value.execute.return_value = mock_tools_result

        with patch("diagnostic_mcp.server.supabase", mock_supabase):
            with patch("diagnostic_mcp.server.parse_mcp_servers") as mock_parse:
                mock_parse.return_value = {
                    "mcpServers": {
                        "knowledge-mcp": {"command": "uvx"},
                        "github-mcp": {"command": "uvx"},
                        "vast-mcp": {"command": "uvx"},  # Configured but no tools
                    }
                }

                result = await handle_check_tool_callability({})

                assert len(result) == 1
                import json
                response = json.loads(result[0].text)

                assert response["ok"] is True
                data = response["data"]

                # Should have 2 callable (knowledge, github) and 1 not callable (vast)
                assert data["summary"]["callable_count"] == 2
                assert data["summary"]["not_callable_count"] == 1


class TestNamespaceVerification:
    """Tests for check_namespace_verification handler."""

    @pytest.mark.asyncio
    async def test_no_supabase_connection(self):
        """Test error when Supabase is not available."""
        with patch("diagnostic_mcp.server.supabase", None):
            result = await handle_check_namespace_verification({})

            assert len(result) == 1
            import json
            response = json.loads(result[0].text)

            assert response["ok"] is False
            assert "Supabase connection not available" in response["message"]


class TestRealInvocation:
    """Tests for check_real_invocation handler."""

    @pytest.mark.asyncio
    async def test_empty_servers_filter(self):
        """Test with no servers to test."""
        with patch("diagnostic_mcp.server.parse_mcp_servers") as mock_parse:
            mock_parse.return_value = {"mcpServers": {}}

            result = await handle_check_real_invocation({"servers": []})

            assert len(result) == 1
            import json
            response = json.loads(result[0].text)

            assert response["ok"] is True
            data = response["data"]
            assert data["summary"]["total_tested"] == 0


class TestToolIntegration:
    """Tests for check_tool_integration handler."""

    @pytest.mark.asyncio
    async def test_integration_combines_all_checks(self):
        """Test that integration check runs all three checks."""
        # Mock all three handlers
        mock_callability = [MagicMock()]
        mock_callability[0].text = '{"ok": true, "data": {"summary": {"not_callable_count": 0}}}'

        mock_namespace = [MagicMock()]
        mock_namespace[0].text = '{"ok": true, "data": {"summary": {"issues_found": 0}}}'

        mock_invocation = [MagicMock()]
        mock_invocation[0].text = '{"ok": true, "data": {"summary": {"error": 0, "timeout": 0}}}'

        with patch("diagnostic_mcp.server.handle_check_tool_callability", return_value=mock_callability):
            with patch("diagnostic_mcp.server.handle_check_namespace_verification", return_value=mock_namespace):
                with patch("diagnostic_mcp.server.handle_check_real_invocation", return_value=mock_invocation):
                    result = await handle_check_tool_integration({})

                    assert len(result) == 1
                    import json
                    response = json.loads(result[0].text)

                    assert response["ok"] is True
                    data = response["data"]

                    # Should have all three check results
                    assert "callability_check" in data
                    assert "namespace_check" in data
                    assert "invocation_check" in data
                    assert data["summary"]["checks_run"] == 3
                    assert data["overall_health"] == "healthy"
                    assert data["summary"]["configuration_issues_count"] == 0
                    assert data["configuration_health"] == "healthy"
