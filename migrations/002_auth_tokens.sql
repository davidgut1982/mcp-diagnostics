-- Migration: Add auth_tokens table for session token authentication
-- Date: 2025-12-21
-- Purpose: Support session token-based authentication for diagnostic-mcp HTTP server

-- Create auth_tokens table
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_id UUID PRIMARY KEY,
    token_hash TEXT NOT NULL,  -- SHA256 hash of the token
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb,

    -- Constraints
    CONSTRAINT token_hash_not_empty CHECK (LENGTH(token_hash) > 0),
    CONSTRAINT expires_after_created CHECK (expires_at > created_at)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_auth_tokens_token_hash ON auth_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_expires_at ON auth_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_revoked_at ON auth_tokens(revoked_at) WHERE revoked_at IS NULL;

-- Create index for active token queries (not revoked + not expired)
CREATE INDEX IF NOT EXISTS idx_auth_tokens_active ON auth_tokens(expires_at, revoked_at)
    WHERE revoked_at IS NULL;

-- Add comments for documentation
COMMENT ON TABLE auth_tokens IS 'Session tokens for diagnostic-mcp HTTP server authentication';
COMMENT ON COLUMN auth_tokens.token_id IS 'Unique token identifier (UUID)';
COMMENT ON COLUMN auth_tokens.token_hash IS 'SHA256 hash of the plaintext token';
COMMENT ON COLUMN auth_tokens.created_at IS 'Token creation timestamp';
COMMENT ON COLUMN auth_tokens.expires_at IS 'Token expiration timestamp';
COMMENT ON COLUMN auth_tokens.revoked_at IS 'Token revocation timestamp (NULL if active)';
COMMENT ON COLUMN auth_tokens.metadata IS 'Optional metadata (client info, purpose, etc.)';

-- Create function to cleanup expired tokens
CREATE OR REPLACE FUNCTION cleanup_expired_auth_tokens()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM auth_tokens
    WHERE expires_at < NOW();

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cleanup_expired_auth_tokens() IS 'Delete expired auth tokens and return count removed';

-- Row Level Security (RLS) - Optional, enable if needed
-- ALTER TABLE auth_tokens ENABLE ROW LEVEL SECURITY;

-- Grant permissions (adjust based on your Supabase service role)
-- GRANT SELECT, INSERT, UPDATE, DELETE ON auth_tokens TO service_role;
