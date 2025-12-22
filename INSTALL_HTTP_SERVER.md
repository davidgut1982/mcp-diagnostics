# Installing diagnostic-mcp HTTP Server with Health Probes

## Quick Install

```bash
# 1. Install systemd service
sudo cp /srv/latvian_mcp/servers/diagnostic-mcp/diagnostic-mcp-http.service /etc/systemd/system/

# 2. Reload systemd
sudo systemctl daemon-reload

# 3. Enable service (start on boot)
sudo systemctl enable diagnostic-mcp-http

# 4. Start service
sudo systemctl start diagnostic-mcp-http

# 5. Check status
sudo systemctl status diagnostic-mcp-http
```

## Verify Installation

```bash
# Check if server is running
curl http://localhost:5555/health

# Check startup probe
curl http://localhost:5555/health?startup

# Check liveness probe
curl http://localhost:5555/health?live

# Check readiness probe
curl http://localhost:5555/health?ready

# Check comprehensive status
curl http://localhost:5555/health?status | jq

# Check server info
curl http://localhost:5555/info | jq
```

## Configuration

### Default Configuration

The service runs with these defaults:
- Port: 5555
- Host: 0.0.0.0 (all interfaces)
- Startup duration: 30 seconds
- Allowed rejections: 100 per 10s
- Sampling interval: 10 seconds
- Degraded threshold: 25% error rate

### Custom Configuration

Edit the service file to customize:

```bash
sudo systemctl edit diagnostic-mcp-http --full
```

Modify the `ExecStart` line:

```ini
ExecStart=/usr/bin/python3 /srv/latvian_mcp/servers/diagnostic-mcp/http_server.py \
    --port 5555 \
    --host 0.0.0.0 \
    --startup-duration 60 \
    --allowed-rejections 200 \
    --sampling-interval 30 \
    --degraded-threshold 0.10
```

Then reload:

```bash
sudo systemctl daemon-reload
sudo systemctl restart diagnostic-mcp-http
```

### Environment Variables

Create `.env` file for sensitive configuration:

```bash
cd /srv/latvian_mcp/servers/diagnostic-mcp
cat > .env << 'EOF'
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key
SENTRY_DSN=your-sentry-dsn
SENTRY_ENVIRONMENT=production
EOF

chmod 600 .env
```

The service will automatically load these variables.

## Monitoring

### Check Logs

```bash
# Follow logs in real-time
sudo journalctl -u diagnostic-mcp-http -f

# Show recent logs
sudo journalctl -u diagnostic-mcp-http -n 100

# Show logs since boot
sudo journalctl -u diagnostic-mcp-http -b
```

### Check Probe Status

```bash
# Quick status check
python /srv/latvian_mcp/servers/diagnostic-mcp/cli.py --check probes --format summary

# Detailed probe status
python /srv/latvian_mcp/servers/diagnostic-mcp/cli.py --check probes

# JSON output for automation
python /srv/latvian_mcp/servers/diagnostic-mcp/cli.py --check probes --format json
```

### Continuous Monitoring

```bash
# Monitor probes every 5 seconds
watch -n 5 'curl -s http://localhost:5555/health?status | jq ".overall_status"'

# Monitor with CLI
while true; do
  python /srv/latvian_mcp/servers/diagnostic-mcp/cli.py --check probes --format summary
  sleep 5
done
```

## Troubleshooting

### Service Won't Start

```bash
# Check service status
sudo systemctl status diagnostic-mcp-http

# Check for errors
sudo journalctl -u diagnostic-mcp-http -n 50

# Test manually
cd /srv/latvian_mcp/servers/diagnostic-mcp
python http_server.py
```

### Port Already in Use

```bash
# Find what's using port 5555
sudo lsof -i :5555

# Kill the process
sudo kill -9 <PID>

# Or change the port in service file
sudo systemctl edit diagnostic-mcp-http --full
# Change --port 5555 to --port 5556
sudo systemctl daemon-reload
sudo systemctl restart diagnostic-mcp-http
```

### Probes Showing DOWN

```bash
# Check comprehensive status
curl http://localhost:5555/health?status | jq

# Check startup probe (should be UP after 30s)
curl http://localhost:5555/health?startup | jq

# Check readiness probe
curl http://localhost:5555/health?ready | jq

# Check liveness probe
curl http://localhost:5555/health?live | jq
```

### High Error Rate / Degraded

```bash
# Check metrics
curl http://localhost:5555/health?ready | jq '.metrics'

# Adjust degraded threshold if needed
sudo systemctl edit diagnostic-mcp-http --full
# Change --degraded-threshold 0.25 to --degraded-threshold 0.50
sudo systemctl daemon-reload
sudo systemctl restart diagnostic-mcp-http
```

## Integration with Load Balancers

### NGINX

Add health check to upstream:

```nginx
upstream diagnostic_mcp {
    server localhost:5555 max_fails=3 fail_timeout=30s;
    check interval=5000 rise=2 fall=3 timeout=1000 type=http;
    check_http_send "GET /health?ready HTTP/1.0\r\n\r\n";
    check_http_expect_alive http_2xx;
}

server {
    listen 80;
    server_name diagnostic-mcp.example.com;

    location / {
        proxy_pass http://diagnostic_mcp;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Expose health endpoint
    location /health {
        proxy_pass http://diagnostic_mcp/health;
    }
}
```

### HAProxy

Add backend health check:

```haproxy
backend diagnostic_mcp
    mode http
    balance roundrobin
    option httpchk GET /health?ready
    http-check expect status 200
    server server1 localhost:5555 check inter 5s fall 3 rise 2
```

### Kubernetes

See `HEALTH_PROBES.md` for Kubernetes integration example.

## Uninstall

```bash
# Stop and disable service
sudo systemctl stop diagnostic-mcp-http
sudo systemctl disable diagnostic-mcp-http

# Remove service file
sudo rm /etc/systemd/system/diagnostic-mcp-http.service

# Reload systemd
sudo systemctl daemon-reload
```

## Advanced Configuration

### Running Multiple Instances

Run multiple instances on different ports:

```bash
# Instance 1 (port 5555)
python http_server.py --port 5555

# Instance 2 (port 5556)
python http_server.py --port 5556
```

Create separate service files:
- `diagnostic-mcp-http-1.service` (port 5555)
- `diagnostic-mcp-http-2.service` (port 5556)

### Custom Probe Thresholds

For high-traffic environments:

```bash
python http_server.py \
  --startup-duration 60 \
  --allowed-rejections 500 \
  --sampling-interval 60 \
  --recovery-interval 120 \
  --degraded-threshold 0.30
```

For strict production:

```bash
python http_server.py \
  --startup-duration 30 \
  --allowed-rejections 50 \
  --sampling-interval 5 \
  --recovery-interval 10 \
  --degraded-threshold 0.10
```

## Security Considerations

1. **Firewall Rules**
   ```bash
   # Allow only from localhost
   sudo ufw allow from 127.0.0.1 to any port 5555

   # Or allow from specific subnet
   sudo ufw allow from 10.0.0.0/24 to any port 5555
   ```

2. **Bind to Localhost Only**
   ```bash
   # Edit service to use 127.0.0.1 instead of 0.0.0.0
   python http_server.py --host 127.0.0.1
   ```

3. **Use Reverse Proxy**
   - Run nginx/haproxy in front
   - Add authentication at proxy level
   - Use TLS/SSL

## Performance Tuning

### Resource Limits

Edit service file:

```ini
[Service]
LimitNOFILE=65536      # File descriptor limit
MemoryMax=512M         # Maximum memory
CPUQuota=50%           # Maximum CPU usage
```

### Logging

Reduce logging level:

```bash
# Set log level to WARNING (less verbose)
export LOG_LEVEL=WARNING
python http_server.py
```

Or modify the service file to add:

```ini
Environment="LOG_LEVEL=WARNING"
```

## References

- Full documentation: [HEALTH_PROBES.md](HEALTH_PROBES.md)
- Test suite: [tests/test_health_monitor.py](tests/test_health_monitor.py)
- Main README: [README.md](README.md)
- Implementation summary: [PHASE3_COMPLETION_SUMMARY.md](PHASE3_COMPLETION_SUMMARY.md)
