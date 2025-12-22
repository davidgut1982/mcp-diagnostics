#!/usr/bin/env python3
"""
Tests for authentication module.

Tests cover:
- Token generation and validation
- Storage backends (memory and Supabase mock)
- Rate limiting
- Token expiration
- MCP tools for token management
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch

# Import auth components
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from diagnostic_mcp.auth import (
    AuthManager,
    MemoryTokenStorage,
    SupabaseTokenStorage,
    SessionToken,
    RateLimiter
)


class TestMemoryTokenStorage:
    """Test in-memory token storage."""

    @pytest.mark.asyncio
    async def test_create_and_get_token(self):
        """Test creating and retrieving a token."""
        storage = MemoryTokenStorage()

        token = SessionToken(
            token_id="test-id",
            token_hash="test-hash",
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=24)
        )

        success = await storage.create_token(token)
        assert success is True

        retrieved = await storage.get_token("test-id")
        assert retrieved is not None
        assert retrieved.token_id == "test-id"
        assert retrieved.token_hash == "test-hash"

    @pytest.mark.asyncio
    async def test_revoke_token(self):
        """Test revoking a token."""
        storage = MemoryTokenStorage()

        token = SessionToken(
            token_id="test-id",
            token_hash="test-hash",
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=24)
        )

        await storage.create_token(token)
        success = await storage.revoke_token("test-id")
        assert success is True

        retrieved = await storage.get_token("test-id")
        assert retrieved.revoked_at is not None

    @pytest.mark.asyncio
    async def test_list_active_tokens(self):
        """Test listing active tokens."""
        storage = MemoryTokenStorage()

        # Create active token
        active = SessionToken(
            token_id="active",
            token_hash="hash1",
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=24)
        )

        # Create expired token
        expired = SessionToken(
            token_id="expired",
            token_hash="hash2",
            created_at=datetime.now() - timedelta(hours=48),
            expires_at=datetime.now() - timedelta(hours=24)
        )

        # Create revoked token
        revoked = SessionToken(
            token_id="revoked",
            token_hash="hash3",
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=24),
            revoked_at=datetime.now()
        )

        await storage.create_token(active)
        await storage.create_token(expired)
        await storage.create_token(revoked)

        active_tokens = await storage.list_active_tokens()

        # Only the active token should be returned
        assert len(active_tokens) == 1
        assert active_tokens[0].token_id == "active"

    @pytest.mark.asyncio
    async def test_cleanup_expired(self):
        """Test cleanup of expired tokens."""
        storage = MemoryTokenStorage()

        # Create expired token
        expired = SessionToken(
            token_id="expired",
            token_hash="hash",
            created_at=datetime.now() - timedelta(hours=48),
            expires_at=datetime.now() - timedelta(hours=24)
        )

        await storage.create_token(expired)
        count = await storage.cleanup_expired()

        assert count == 1
        assert await storage.get_token("expired") is None


class TestRateLimiter:
    """Test rate limiting."""

    def test_rate_limit_allows_within_limit(self):
        """Test that requests within limit are allowed."""
        limiter = RateLimiter(max_attempts=3, window_seconds=60)

        assert limiter.is_allowed("client1") is True
        assert limiter.is_allowed("client1") is True
        assert limiter.is_allowed("client1") is True

    def test_rate_limit_blocks_over_limit(self):
        """Test that requests over limit are blocked."""
        limiter = RateLimiter(max_attempts=3, window_seconds=60)

        # First 3 should succeed
        for _ in range(3):
            assert limiter.is_allowed("client1") is True

        # 4th should fail
        assert limiter.is_allowed("client1") is False

    def test_rate_limit_per_client(self):
        """Test that rate limiting is per-client."""
        limiter = RateLimiter(max_attempts=2, window_seconds=60)

        # Client 1 uses up their limit
        assert limiter.is_allowed("client1") is True
        assert limiter.is_allowed("client1") is True
        assert limiter.is_allowed("client1") is False

        # Client 2 should still be allowed
        assert limiter.is_allowed("client2") is True


class TestAuthManager:
    """Test authentication manager."""

    @pytest.mark.asyncio
    async def test_create_token(self):
        """Test token creation."""
        storage = MemoryTokenStorage()
        auth_manager = AuthManager(
            storage=storage,
            admin_token="admin-secret",
            default_ttl_hours=24
        )

        result = await auth_manager.create_token(
            client_id="test-client",
            ttl_hours=48
        )

        assert result is not None
        assert "token" in result
        assert "token_id" in result
        assert "expires_at" in result
        assert result["ttl_hours"] == 48

    @pytest.mark.asyncio
    async def test_validate_admin_token(self):
        """Test admin token validation."""
        storage = MemoryTokenStorage()
        auth_manager = AuthManager(
            storage=storage,
            admin_token="admin-secret"
        )

        # Valid admin token
        assert await auth_manager.validate_token("admin-secret") is True

        # Invalid admin token
        assert await auth_manager.validate_token("wrong-secret") is False

    @pytest.mark.asyncio
    async def test_validate_session_token(self):
        """Test session token validation."""
        storage = MemoryTokenStorage()
        auth_manager = AuthManager(
            storage=storage,
            admin_token="admin-secret"
        )

        # Create a session token
        result = await auth_manager.create_token(
            client_id="test-client",
            ttl_hours=24
        )

        token = result["token"]

        # Valid session token
        assert await auth_manager.validate_token(token) is True

        # Invalid session token
        assert await auth_manager.validate_token("invalid-token") is False

    @pytest.mark.asyncio
    async def test_validate_expired_token(self):
        """Test that expired tokens are rejected."""
        storage = MemoryTokenStorage()
        auth_manager = AuthManager(storage=storage)

        # Create expired token manually
        from diagnostic_mcp.auth import SessionToken
        import hashlib

        token_value = "test-token"
        token_hash = hashlib.sha256(token_value.encode()).hexdigest()

        expired_token = SessionToken(
            token_id="expired-id",
            token_hash=token_hash,
            created_at=datetime.now() - timedelta(hours=48),
            expires_at=datetime.now() - timedelta(hours=24)
        )

        await storage.create_token(expired_token)

        # Should reject expired token
        assert await auth_manager.validate_token(token_value) is False

    @pytest.mark.asyncio
    async def test_revoke_token(self):
        """Test token revocation."""
        storage = MemoryTokenStorage()
        auth_manager = AuthManager(storage=storage)

        # Create token
        result = await auth_manager.create_token(
            client_id="test-client"
        )

        token = result["token"]
        token_id = result["token_id"]

        # Verify token is valid
        assert await auth_manager.validate_token(token) is True

        # Revoke token
        await auth_manager.revoke_token(token_id)

        # Verify token is now invalid
        assert await auth_manager.validate_token(token) is False

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Test rate limiting on token creation."""
        storage = MemoryTokenStorage()
        rate_limiter = RateLimiter(max_attempts=2, window_seconds=60)
        auth_manager = AuthManager(
            storage=storage,
            rate_limiter=rate_limiter
        )

        # First 2 should succeed
        result1 = await auth_manager.create_token(client_id="client1")
        assert result1 is not None

        result2 = await auth_manager.create_token(client_id="client1")
        assert result2 is not None

        # 3rd should fail (rate limited)
        result3 = await auth_manager.create_token(client_id="client1")
        assert result3 is None

    @pytest.mark.asyncio
    async def test_list_active_tokens(self):
        """Test listing active tokens."""
        storage = MemoryTokenStorage()
        auth_manager = AuthManager(storage=storage)

        # Create multiple tokens
        await auth_manager.create_token(client_id="client1")
        await auth_manager.create_token(client_id="client2")
        await auth_manager.create_token(client_id="client3")

        tokens = await auth_manager.list_active_tokens()

        assert len(tokens) == 3
        # Tokens should not include plaintext token values
        for token in tokens:
            assert "token" not in token
            assert "token_id" in token
            assert "created_at" in token
            assert "expires_at" in token


class TestSupabaseTokenStorage:
    """Test Supabase token storage (mocked)."""

    @pytest.mark.asyncio
    async def test_create_token(self):
        """Test creating token in Supabase."""
        mock_supabase = Mock()
        mock_supabase.table.return_value.insert.return_value.execute.return_value = Mock()

        storage = SupabaseTokenStorage(mock_supabase)

        token = SessionToken(
            token_id="test-id",
            token_hash="test-hash",
            created_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=24)
        )

        success = await storage.create_token(token)

        assert success is True
        mock_supabase.table.assert_called_with("auth_tokens")

    @pytest.mark.asyncio
    async def test_get_token(self):
        """Test retrieving token from Supabase."""
        mock_supabase = Mock()

        # Mock response
        mock_data = {
            "token_id": "test-id",
            "token_hash": "test-hash",
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat(),
            "revoked_at": None,
            "metadata": {}
        }

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [mock_data]

        storage = SupabaseTokenStorage(mock_supabase)
        token = await storage.get_token("test-id")

        assert token is not None
        assert token.token_id == "test-id"
        assert token.token_hash == "test-hash"


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
