#!/bin/bash
# Force uvx to rebuild an MCP server package by clearing all caches
# Usage: ./force_rebuild.sh <package-name> [--force]
# Example: ./force_rebuild.sh diagnostic-mcp
# Example: ./force_rebuild.sh diagnostic-mcp --force

set -e  # Exit on error

PACKAGE_NAME="${1:-}"
FORCE_FLAG="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_ROOT="$(dirname "$SCRIPT_DIR")"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    echo "Usage: $0 <package-name> [--force]"
    echo ""
    echo "Force uvx to rebuild an MCP server package by clearing all caches."
    echo ""
    echo "Options:"
    echo "  --force    Force cache clear even if uv is in use (when Claude Code is running)"
    echo ""
    echo "Examples:"
    echo "  $0 diagnostic-mcp"
    echo "  $0 knowledge-mcp --force"
    echo ""
    echo "This script will:"
    echo "  1. Clear local build artifacts (build/, dist/, *.egg-info)"
    echo "  2. Clear uv cache for the specific package"
    echo "  3. Remind you to restart Claude Code"
    echo ""
    echo "Note: Without --force, script may fail if Claude Code is running"
    echo "      (uv cache will be 'in-use'). Use --force to override."
    exit 1
}

if [ -z "$PACKAGE_NAME" ]; then
    echo -e "${RED}Error: Package name required${NC}"
    usage
fi

echo -e "${GREEN}=== Force Rebuild: $PACKAGE_NAME ===${NC}"
echo ""

# Step 1: Clear local build artifacts
echo -e "${YELLOW}Step 1: Clearing local build artifacts...${NC}"
if [ -d "$SERVER_ROOT" ]; then
    cd "$SERVER_ROOT"

    # Remove build directories
    for dir in build dist .pytest_cache __pycache__ .ruff_cache; do
        if [ -d "$dir" ]; then
            echo "  Removing $dir/"
            rm -rf "$dir"
        fi
    done

    # Remove egg-info directories (both in root and src/)
    find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

    echo -e "${GREEN}  ✓ Local artifacts cleared${NC}"
else
    echo -e "${YELLOW}  ! Server root not found: $SERVER_ROOT${NC}"
fi
echo ""

# Step 2: Clear uv cache for specific package
echo -e "${YELLOW}Step 2: Clearing uv cache for $PACKAGE_NAME...${NC}"

# Check if uv command exists
if ! command -v uv &> /dev/null; then
    echo -e "${RED}  ✗ uv command not found${NC}"
    echo -e "${YELLOW}  Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh${NC}"
    exit 1
fi

# Build uv cache clean command
UV_CMD="uv cache clean"
if [ "$FORCE_FLAG" = "--force" ]; then
    UV_CMD="$UV_CMD --force"
    echo "  Using --force flag (cache in-use will be overridden)"
fi
UV_CMD="$UV_CMD $PACKAGE_NAME"

# Clear package-specific cache
echo "  Running: $UV_CMD"
if eval "$UV_CMD" 2>&1; then
    echo -e "${GREEN}  ✓ uv cache cleared for $PACKAGE_NAME${NC}"
else
    CACHE_STATUS=$?
    echo -e "${YELLOW}  ! Cache clear failed (exit code: $CACHE_STATUS)${NC}"
    if [ "$FORCE_FLAG" != "--force" ]; then
        echo -e "${YELLOW}  Tip: If cache is in-use, try: $0 $PACKAGE_NAME --force${NC}"
    fi
fi
echo ""

# Step 3: Remind about Claude Code restart
echo -e "${YELLOW}Step 3: REQUIRED - Restart Claude Code${NC}"
echo ""
echo -e "${RED}IMPORTANT:${NC} The changes won't take effect until you:"
echo "  1. Save any work in progress"
echo "  2. Completely QUIT Claude Code (not just restart session)"
echo "  3. Restart Claude Code"
echo ""
echo -e "${GREEN}After restart:${NC} Claude Code will spawn new MCP server processes"
echo "with the updated code."
echo ""
echo -e "${GREEN}=== Rebuild preparation complete ===${NC}"
echo ""
echo "Next: Quit and restart Claude Code to apply changes"
