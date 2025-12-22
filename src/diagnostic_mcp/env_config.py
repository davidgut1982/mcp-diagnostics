"""
Environment configuration for diagnostic-mcp server.

Loads environment variables from:
1. /srv/latvian_mcp/.env file (if it exists)
2. System environment variables (which override .env values)
"""

import os
from pathlib import Path
from typing import Optional

# Path to centralized .env file
ENV_FILE = Path("/srv/latvian_mcp/.env")


def load_env_file():
    """Load environment variables from .env file if it exists."""
    if not ENV_FILE.exists():
        return

    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()

            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            # Parse KEY=VALUE
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                # Only set if not already in environment
                if key not in os.environ:
                    os.environ[key] = value


# Load .env file when module is imported
load_env_file()


def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get environment variable with optional default."""
    return os.getenv(key, default)


def require_env(key: str) -> str:
    """Get required environment variable or raise ValueError."""
    value = os.getenv(key)
    if value is None:
        raise ValueError(f"{key} environment variable is required but not set")
    return value
