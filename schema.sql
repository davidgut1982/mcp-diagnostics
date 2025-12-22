-- Diagnostic History Schema for diagnostic-mcp
-- Stores diagnostic run results for trend analysis and monitoring

CREATE TABLE IF NOT EXISTS diagnostic_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Diagnostic metadata
    check_type TEXT NOT NULL,  -- 'all', 'health', 'ports', 'config', 'tools'
    triggered_by TEXT,          -- 'cli', 'http', 'scheduled', 'manual'

    -- Overall status
    status TEXT NOT NULL,       -- 'healthy', 'degraded', 'critical', 'error'
    total_issues INTEGER NOT NULL DEFAULT 0,
    critical_issues INTEGER NOT NULL DEFAULT 0,

    -- Detailed results (JSONB for flexible querying)
    port_check_result JSONB,
    health_check_result JSONB,
    config_check_result JSONB,
    tool_check_result JSONB,

    -- Summary statistics (for fast querying without parsing JSONB)
    servers_total INTEGER,
    servers_online INTEGER,
    servers_offline INTEGER,
    servers_partial INTEGER,     -- stdio fails but HTTP works
    port_conflicts INTEGER,
    config_issues INTEGER,
    tool_conflicts INTEGER,

    -- Enhanced diagnostics v2 metadata
    detected_dual_transports INTEGER DEFAULT 0,  -- servers with working HTTP despite stdio failure
    venv_issues INTEGER DEFAULT 0,                -- servers with venv validation failures

    -- Execution metadata
    execution_time_ms INTEGER,   -- how long the diagnostic took
    timeout_seconds INTEGER,      -- timeout used for health checks

    -- Indexing for common queries
    INDEX idx_diagnostic_history_created_at ON diagnostic_history(created_at DESC),
    INDEX idx_diagnostic_history_status ON diagnostic_history(status),
    INDEX idx_diagnostic_history_check_type ON diagnostic_history(check_type)
);

-- Enable Row Level Security (RLS)
ALTER TABLE diagnostic_history ENABLE ROW LEVEL SECURITY;

-- Policy: Allow all operations for authenticated users
-- (Adjust based on your security requirements)
CREATE POLICY "Allow all operations for authenticated users"
ON diagnostic_history
FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

-- Policy: Allow read-only for service role
CREATE POLICY "Allow read for service role"
ON diagnostic_history
FOR SELECT
TO service_role
USING (true);

-- View for latest diagnostic per check type
CREATE OR REPLACE VIEW diagnostic_latest AS
SELECT DISTINCT ON (check_type)
    id,
    created_at,
    check_type,
    status,
    total_issues,
    critical_issues,
    servers_total,
    servers_online,
    servers_offline,
    servers_partial,
    detected_dual_transports
FROM diagnostic_history
ORDER BY check_type, created_at DESC;

-- View for trend analysis (hourly aggregates)
CREATE OR REPLACE VIEW diagnostic_trends_hourly AS
SELECT
    date_trunc('hour', created_at) AS hour,
    check_type,
    COUNT(*) AS run_count,
    AVG(servers_offline) AS avg_servers_offline,
    AVG(servers_partial) AS avg_servers_partial,
    AVG(critical_issues) AS avg_critical_issues,
    MAX(servers_offline) AS max_servers_offline,
    SUM(CASE WHEN status = 'critical' THEN 1 ELSE 0 END) AS critical_runs
FROM diagnostic_history
GROUP BY date_trunc('hour', created_at), check_type
ORDER BY hour DESC, check_type;

-- Function to clean up old diagnostic history (optional, for maintenance)
CREATE OR REPLACE FUNCTION cleanup_old_diagnostics(days_to_keep INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM diagnostic_history
    WHERE created_at < NOW() - (days_to_keep || ' days')::INTERVAL;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON TABLE diagnostic_history IS 'Stores diagnostic run results for trend analysis and monitoring';
COMMENT ON COLUMN diagnostic_history.check_type IS 'Type of check: all, health, ports, config, tools';
COMMENT ON COLUMN diagnostic_history.status IS 'Overall status: healthy, degraded, critical, error';
COMMENT ON COLUMN diagnostic_history.servers_partial IS 'Servers where stdio fails but HTTP works (enhanced diagnostics v2)';
COMMENT ON COLUMN diagnostic_history.detected_dual_transports IS 'Count of servers with working HTTP despite stdio failure';
