#!/usr/bin/env python3
"""
Session Token Authentication for diagnostic-mcp HTTP Server

Provides session token-based authentication for production deployments.

Features:
- Secure session token generation (UUID v4)
- Token storage (in-memory or Supabase)
- Token expiration and revocation
- Admin token bootstrapping
- Rate limiting for token creation
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class SessionToken:
    """Represents an authenticated session token."""
    token_id: str
    token_hash: str  # SHA256 hash of the actual token
    created_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class RateLimiter:
    """
    Simple in-memory rate limiter for token creation.

    Tracks token creation attempts per IP/client.
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window = timedelta(seconds=window_seconds)
        self.attempts: Dict[str, List[datetime]] = defaultdict(list)

    def is_allowed(self, client_id: str) -> bool:
        """Check if client is allowed to create token."""
        now = datetime.now()

        # Remove old attempts outside the window
        cutoff = now - self.window
        self.attempts[client_id] = [
            attempt for attempt in self.attempts[client_id]
            if attempt > cutoff
        ]

        # Check if under limit
        if len(self.attempts[client_id]) >= self.max_attempts:
            return False

        # Record this attempt
        self.attempts[client_id].append(now)
        return True


class TokenStorage:
    """Base class for token storage backends."""

    async def create_token(self, token: SessionToken) -> bool:
        """Store a new token. Returns True if successful."""
        raise NotImplementedError

    async def get_token(self, token_id: str) -> Optional[SessionToken]:
        """Retrieve token by ID."""
        raise NotImplementedError

    async def revoke_token(self, token_id: str) -> bool:
        """Mark token as revoked. Returns True if successful."""
        raise NotImplementedError

    async def list_active_tokens(self) -> List[SessionToken]:
        """List all active (non-expired, non-revoked) tokens."""
        raise NotImplementedError

    async def cleanup_expired(self) -> int:
        """Remove expired tokens. Returns number removed."""
        raise NotImplementedError


class MemoryTokenStorage(TokenStorage):
    """In-memory token storage (not persistent across restarts)."""

    def __init__(self):
        self.tokens: Dict[str, SessionToken] = {}

    async def create_token(self, token: SessionToken) -> bool:
        """Store token in memory."""
        self.tokens[token.token_id] = token
        logger.info(f"Token created (memory): {token.token_id}")
        return True

    async def get_token(self, token_id: str) -> Optional[SessionToken]:
        """Retrieve token from memory."""
        return self.tokens.get(token_id)

    async def revoke_token(self, token_id: str) -> bool:
        """Mark token as revoked."""
        token = self.tokens.get(token_id)
        if token:
            token.revoked_at = datetime.now()
            logger.info(f"Token revoked (memory): {token_id}")
            return True
        return False

    async def list_active_tokens(self) -> List[SessionToken]:
        """List active tokens."""
        now = datetime.now()
        return [
            token for token in self.tokens.values()
            if token.revoked_at is None and token.expires_at > now
        ]

    async def cleanup_expired(self) -> int:
        """Remove expired tokens from memory."""
        now = datetime.now()
        expired_ids = [
            token_id for token_id, token in self.tokens.items()
            if token.expires_at <= now
        ]

        for token_id in expired_ids:
            del self.tokens[token_id]

        if expired_ids:
            logger.info(f"Cleaned up {len(expired_ids)} expired tokens (memory)")

        return len(expired_ids)


class SupabaseTokenStorage(TokenStorage):
    """Supabase-backed token storage (persistent)."""

    def __init__(self, supabase_client):
        self.supabase = supabase_client

    async def create_token(self, token: SessionToken) -> bool:
        """Store token in Supabase."""
        try:
            data = {
                "token_id": token.token_id,
                "token_hash": token.token_hash,
                "created_at": token.created_at.isoformat(),
                "expires_at": token.expires_at.isoformat(),
                "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
                "metadata": token.metadata or {}
            }

            self.supabase.table("auth_tokens").insert(data).execute()
            logger.info(f"Token created (Supabase): {token.token_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to create token in Supabase: {e}")
            return False

    async def get_token(self, token_id: str) -> Optional[SessionToken]:
        """Retrieve token from Supabase."""
        try:
            result = self.supabase.table("auth_tokens").select("*").eq("token_id", token_id).execute()

            if not result.data:
                return None

            data = result.data[0]
            return SessionToken(
                token_id=data["token_id"],
                token_hash=data["token_hash"],
                created_at=datetime.fromisoformat(data["created_at"]),
                expires_at=datetime.fromisoformat(data["expires_at"]),
                revoked_at=datetime.fromisoformat(data["revoked_at"]) if data.get("revoked_at") else None,
                metadata=data.get("metadata")
            )
        except Exception as e:
            logger.error(f"Failed to get token from Supabase: {e}")
            return None

    async def revoke_token(self, token_id: str) -> bool:
        """Mark token as revoked in Supabase."""
        try:
            self.supabase.table("auth_tokens").update({
                "revoked_at": datetime.now().isoformat()
            }).eq("token_id", token_id).execute()

            logger.info(f"Token revoked (Supabase): {token_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to revoke token in Supabase: {e}")
            return False

    async def list_active_tokens(self) -> List[SessionToken]:
        """List active tokens from Supabase."""
        try:
            now = datetime.now().isoformat()
            result = self.supabase.table("auth_tokens")\
                .select("*")\
                .is_("revoked_at", "null")\
                .gt("expires_at", now)\
                .execute()

            tokens = []
            for data in result.data:
                tokens.append(SessionToken(
                    token_id=data["token_id"],
                    token_hash=data["token_hash"],
                    created_at=datetime.fromisoformat(data["created_at"]),
                    expires_at=datetime.fromisoformat(data["expires_at"]),
                    revoked_at=None,
                    metadata=data.get("metadata")
                ))

            return tokens
        except Exception as e:
            logger.error(f"Failed to list active tokens from Supabase: {e}")
            return []

    async def cleanup_expired(self) -> int:
        """Remove expired tokens from Supabase."""
        try:
            now = datetime.now().isoformat()
            result = self.supabase.table("auth_tokens")\
                .delete()\
                .lt("expires_at", now)\
                .execute()

            count = len(result.data) if result.data else 0
            if count > 0:
                logger.info(f"Cleaned up {count} expired tokens (Supabase)")

            return count
        except Exception as e:
            logger.error(f"Failed to cleanup expired tokens from Supabase: {e}")
            return 0


class AuthManager:
    """
    Manages session token authentication.

    Features:
    - Token generation with secure randomness
    - Token validation with constant-time comparison
    - Token expiration handling
    - Admin token bootstrapping
    - Rate limiting
    """

    def __init__(
        self,
        storage: TokenStorage,
        admin_token: Optional[str] = None,
        default_ttl_hours: int = 24,
        rate_limiter: Optional[RateLimiter] = None
    ):
        self.storage = storage
        self.admin_token_hash = self._hash_token(admin_token) if admin_token else None
        self.default_ttl = timedelta(hours=default_ttl_hours)
        self.rate_limiter = rate_limiter or RateLimiter()

    @staticmethod
    def _hash_token(token: str) -> str:
        """Hash token with SHA256."""
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    @staticmethod
    def _compare_constant_time(a: str, b: str) -> bool:
        """Constant-time string comparison to prevent timing attacks."""
        return secrets.compare_digest(a, b)

    def generate_token(self) -> str:
        """Generate a new secure session token."""
        # Use UUID v4 for token generation (122 bits of randomness)
        return str(uuid.uuid4())

    async def create_token(
        self,
        client_id: str,
        ttl_hours: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new session token.

        Args:
            client_id: Client identifier for rate limiting
            ttl_hours: Token TTL in hours (default: 24)
            metadata: Optional metadata to store with token

        Returns:
            dict with token, token_id, expires_at, or None if rate limited
        """
        # Check rate limit
        if not self.rate_limiter.is_allowed(client_id):
            logger.warning(f"Rate limit exceeded for client: {client_id}")
            return None

        # Generate token
        token = self.generate_token()
        token_hash = self._hash_token(token)
        token_id = str(uuid.uuid4())

        # Calculate expiration
        ttl = timedelta(hours=ttl_hours) if ttl_hours else self.default_ttl
        created_at = datetime.now()
        expires_at = created_at + ttl

        # Create session token object
        session_token = SessionToken(
            token_id=token_id,
            token_hash=token_hash,
            created_at=created_at,
            expires_at=expires_at,
            metadata=metadata
        )

        # Store in backend
        success = await self.storage.create_token(session_token)

        if not success:
            logger.error(f"Failed to create token for client: {client_id}")
            return None

        # Return plaintext token (only time it's exposed)
        return {
            "token": token,
            "token_id": token_id,
            "expires_at": expires_at.isoformat(),
            "ttl_hours": ttl.total_seconds() / 3600
        }

    async def validate_token(self, token: str) -> bool:
        """
        Validate a session token.

        Args:
            token: The plaintext token to validate

        Returns:
            True if token is valid, False otherwise
        """
        # Check admin token first (if configured)
        if self.admin_token_hash:
            token_hash = self._hash_token(token)
            if self._compare_constant_time(token_hash, self.admin_token_hash):
                logger.debug("Admin token validated")
                return True

        # Hash the token
        token_hash = self._hash_token(token)

        # Search for matching token in storage
        # Note: This is O(n) for memory storage, but acceptable for small token counts
        # For production with many tokens, consider indexing by hash
        active_tokens = await self.storage.list_active_tokens()

        for session_token in active_tokens:
            if self._compare_constant_time(token_hash, session_token.token_hash):
                # Check expiration
                if session_token.expires_at > datetime.now():
                    logger.debug(f"Token validated: {session_token.token_id}")
                    return True
                else:
                    logger.debug(f"Token expired: {session_token.token_id}")
                    return False

        logger.debug("Token not found or invalid")
        return False

    async def revoke_token(self, token_id: str) -> bool:
        """Revoke a token by ID."""
        return await self.storage.revoke_token(token_id)

    async def list_active_tokens(self) -> List[Dict[str, Any]]:
        """List all active tokens (without plaintext tokens)."""
        tokens = await self.storage.list_active_tokens()

        return [
            {
                "token_id": token.token_id,
                "created_at": token.created_at.isoformat(),
                "expires_at": token.expires_at.isoformat(),
                "metadata": token.metadata
            }
            for token in tokens
        ]

    async def cleanup_expired_tokens(self) -> int:
        """Remove expired tokens from storage."""
        return await self.storage.cleanup_expired()
