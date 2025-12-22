#!/bin/bash
# Test all health probe endpoints for diagnostic-mcp HTTP server
# Usage: ./test_probes.sh [port]

PORT=${1:-5555}
BASE_URL="http://localhost:${PORT}"

echo "========================================="
echo "Testing diagnostic-mcp Health Probes"
echo "========================================="
echo "Server: ${BASE_URL}"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to test endpoint
test_endpoint() {
    local name="$1"
    local endpoint="$2"
    local expected_status="${3:-200}"

    echo -e "${BLUE}Testing: ${name}${NC}"
    echo "  Endpoint: ${endpoint}"

    # Make request and capture status code
    response=$(curl -s --max-time 2 --connect-timeout 1 -w "\n%{http_code}" "${BASE_URL}${endpoint}")
    status_code=$(echo "$response" | tail -n 1)
    body=$(echo "$response" | head -n -1)

    # Check status code
    if [ "$status_code" = "$expected_status" ]; then
        echo -e "  Status: ${GREEN}${status_code} ✓${NC}"
    else
        echo -e "  Status: ${RED}${status_code} ✗ (expected ${expected_status})${NC}"
    fi

    # Pretty print JSON response
    if command -v jq &> /dev/null; then
        echo "  Response:"
        echo "$body" | jq '.' | sed 's/^/    /'
    else
        echo "  Response: ${body}"
    fi

    echo ""
}

# Test basic health endpoint
test_endpoint "Basic Health Check" "/health" 200

# Test liveness probe
test_endpoint "Liveness Probe" "/health?live" 200

# Test readiness probe
echo -e "${BLUE}Testing: Readiness Probe${NC}"
echo "  Endpoint: /health?ready"
response=$(curl -s --max-time 2 --connect-timeout 1 -w "\n%{http_code}" "${BASE_URL}/health?ready")
status_code=$(echo "$response" | tail -n 1)
body=$(echo "$response" | head -n -1)

# Readiness can be 200 or 503 depending on state
if [ "$status_code" = "200" ]; then
    echo -e "  Status: ${GREEN}${status_code} (READY) ✓${NC}"
elif [ "$status_code" = "503" ]; then
    echo -e "  Status: ${YELLOW}${status_code} (NOT READY) ⚠${NC}"
else
    echo -e "  Status: ${RED}${status_code} ✗${NC}"
fi

if command -v jq &> /dev/null; then
    echo "  Response:"
    echo "$body" | jq '.' | sed 's/^/    /'

    # Extract key metrics
    probe_status=$(echo "$body" | jq -r '.status // "UNKNOWN"')
    degraded=$(echo "$body" | jq -r '.degraded // false')
    error_rate=$(echo "$body" | jq -r '.metrics.error_rate // 0')

    echo "  Status: ${probe_status}"
    echo "  Degraded: ${degraded}"
    echo "  Error Rate: ${error_rate}"
else
    echo "  Response: ${body}"
fi
echo ""

# Test startup probe
echo -e "${BLUE}Testing: Startup Probe${NC}"
echo "  Endpoint: /health?startup"
response=$(curl -s --max-time 2 --connect-timeout 1 -w "\n%{http_code}" "${BASE_URL}/health?startup")
status_code=$(echo "$response" | tail -n 1)
body=$(echo "$response" | head -n -1)

# Startup can be 200 or 503 depending on startup duration
if [ "$status_code" = "200" ]; then
    echo -e "  Status: ${GREEN}${status_code} (STARTED) ✓${NC}"
elif [ "$status_code" = "503" ]; then
    echo -e "  Status: ${YELLOW}${status_code} (STARTING) ⚠${NC}"
else
    echo -e "  Status: ${RED}${status_code} ✗${NC}"
fi

if command -v jq &> /dev/null; then
    echo "  Response:"
    echo "$body" | jq '.' | sed 's/^/    /'

    # Extract startup info
    startup_complete=$(echo "$body" | jq -r '.startup_complete // false')
    uptime=$(echo "$body" | jq -r '.uptime_seconds // 0')

    echo "  Startup Complete: ${startup_complete}"
    echo "  Uptime: ${uptime}s"
else
    echo "  Response: ${body}"
fi
echo ""

# Test direct startup route
test_endpoint "Startup Probe (Direct Route)" "/health/startup"

# Test comprehensive probe status
echo -e "${BLUE}Testing: Comprehensive Probe Status${NC}"
echo "  Endpoint: /health?status"
response=$(curl -s --max-time 2 --connect-timeout 1 -w "\n%{http_code}" "${BASE_URL}/health?status")
status_code=$(echo "$response" | tail -n 1)
body=$(echo "$response" | head -n -1)

if [ "$status_code" = "200" ] || [ "$status_code" = "503" ]; then
    echo -e "  Status: ${GREEN}${status_code} ✓${NC}"
else
    echo -e "  Status: ${RED}${status_code} ✗${NC}"
fi

if command -v jq &> /dev/null; then
    echo "  Response:"
    echo "$body" | jq '.' | sed 's/^/    /'

    # Extract overall status
    overall_status=$(echo "$body" | jq -r '.overall_status // "unknown"')

    echo ""
    echo "  ========== SUMMARY =========="
    echo "  Overall Status: ${overall_status}"

    # Color code the overall status
    case "$overall_status" in
        "healthy")
            echo -e "  Health: ${GREEN}HEALTHY ✓${NC}"
            ;;
        "starting")
            echo -e "  Health: ${YELLOW}STARTING ⏳${NC}"
            ;;
        "degraded")
            echo -e "  Health: ${YELLOW}DEGRADED ⚠${NC}"
            ;;
        "unready")
            echo -e "  Health: ${YELLOW}UNREADY ⚠${NC}"
            ;;
        "critical")
            echo -e "  Health: ${RED}CRITICAL ✗${NC}"
            ;;
        *)
            echo "  Health: UNKNOWN"
            ;;
    esac

    # Extract probe states
    startup_status=$(echo "$body" | jq -r '.probes.startup.status // "UNKNOWN"')
    liveness_status=$(echo "$body" | jq -r '.probes.liveness.status // "UNKNOWN"')
    readiness_status=$(echo "$body" | jq -r '.probes.readiness.status // "UNKNOWN"')
    is_degraded=$(echo "$body" | jq -r '.probes.readiness.degraded // false')

    echo "  Startup: ${startup_status}"
    echo "  Liveness: ${liveness_status}"
    echo "  Readiness: ${readiness_status}"
    echo "  Degraded: ${is_degraded}"
    echo "  ============================="
else
    echo "  Response: ${body}"
fi
echo ""

# Test direct status route
test_endpoint "Probe Status (Direct Route)" "/health/status"

# Test server info
test_endpoint "Server Info" "/info" 200

# Summary
echo "========================================="
echo "Test Summary"
echo "========================================="

# Check if server is responding
if curl -s -f --max-time 2 --connect-timeout 1 "${BASE_URL}/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Server is responding${NC}"

    # Get overall status
    if command -v jq &> /dev/null; then
        overall=$(curl -s --max-time 2 --connect-timeout 1 "${BASE_URL}/health?status" | jq -r '.overall_status // "unknown"')

        case "$overall" in
            "healthy")
                echo -e "${GREEN}✓ Overall Status: HEALTHY${NC}"
                echo ""
                echo "All systems operational!"
                ;;
            "starting")
                echo -e "${YELLOW}⏳ Overall Status: STARTING${NC}"
                echo ""
                echo "Server is still initializing. Wait for startup to complete."
                ;;
            "degraded")
                echo -e "${YELLOW}⚠ Overall Status: DEGRADED${NC}"
                echo ""
                echo "Server is operational but experiencing issues. Check metrics."
                ;;
            "unready")
                echo -e "${YELLOW}⚠ Overall Status: UNREADY${NC}"
                echo ""
                echo "Server is not ready to accept traffic. Check readiness probe."
                ;;
            "critical")
                echo -e "${RED}✗ Overall Status: CRITICAL${NC}"
                echo ""
                echo "Server has critical issues. Check liveness probe and logs."
                ;;
            *)
                echo "Overall Status: UNKNOWN"
                ;;
        esac
    fi
else
    echo -e "${RED}✗ Server is not responding${NC}"
    echo ""
    echo "Possible issues:"
    echo "  - Server not running (check: systemctl status diagnostic-mcp-http)"
    echo "  - Wrong port (current: ${PORT})"
    echo "  - Firewall blocking connection"
    echo ""
    echo "Try:"
    echo "  sudo systemctl start diagnostic-mcp-http"
    echo "  sudo journalctl -u diagnostic-mcp-http -n 50"
fi

echo "========================================="
