#!/usr/bin/env python3
"""
Diagnostic MCP Server

Provides tools for:
- Port consistency checking across MCP servers
- Health checks for all MCP server SSE endpoints
- Configuration validation for mcp_servers.json
- Tool availability checking across servers
- Comprehensive diagnostic reporting

Spec: LATVIAN_LAB_MCP_MASTER_SPEC_v1.2 § 7.X (Diagnostic Tools)
"""

import os
import sys
import json
import logging
import asyncio
import subprocess
import socket
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple
from datetime import datetime
from collections import defaultdict

import sentry_sdk
import requests
from supabase import create_client, Client
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# Local utilities (bundled for uvx compatibility)
from diagnostic_mcp.response import ResponseEnvelope, ErrorCodes
from diagnostic_mcp.env_config import get_env, require_env
from diagnostic_mcp import trends

# Initialize logging
LOG_DIR = Path("/srv/latvian_mcp/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "diagnostic-mcp.log")
    ]
)
logger = logging.getLogger(__name__)

# Initialize Sentry for monitoring this MCP server
SENTRY_DSN = get_env("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=1.0,
        environment=get_env("SENTRY_ENVIRONMENT", "development"),
        release=get_env("SENTRY_RELEASE", "diagnostic-mcp@0.1.0"),
    )
    logger.info("Sentry monitoring enabled")

# Initialize Supabase client for MCP Index queries
SUPABASE_URL = get_env("SUPABASE_URL")
SUPABASE_KEY = get_env("SUPABASE_KEY")
supabase: Optional[Client] = None

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase client initialized for MCP Index queries")
        # Initialize trends module with Supabase client
        trends.initialize_supabase(supabase)
    except Exception as e:
        logger.warning(f"Failed to initialize Supabase client: {e}")

# Initialize MCP server
app = Server("diagnostic-mcp")

# Configuration
MCP_SERVERS_PATH = Path.home() / ".claude" / "mcp_servers.json"
PORT_RANGE_MIN = 5555
PORT_RANGE_MAX = 5582
SSE_TIMEOUT = 5  # seconds


def format_response(response: dict) -> list[types.TextContent]:
    """Format response as MCP TextContent."""
    return [types.TextContent(type="text", text=json.dumps(response, indent=2))]


def parse_mcp_servers() -> dict:
    """
    Parse ~/.claude/mcp_servers.json and extract MCP server config.

    Returns:
        dict: Settings dictionary with mcpServers configuration

    Raises:
        FileNotFoundError: If mcp_servers.json doesn't exist
        json.JSONDecodeError: If mcp_servers.json is invalid JSON
    """
    if not MCP_SERVERS_PATH.exists():
        raise FileNotFoundError(f"mcp_servers.json not found at {MCP_SERVERS_PATH}")

    with open(MCP_SERVERS_PATH, 'r') as f:
        settings = json.load(f)

    if 'mcpServers' not in settings:
        raise ValueError("mcpServers section not found in mcp_servers.json")

    return settings


def extract_port_map(settings: dict) -> Dict[str, Optional[int]]:
    """
    Extract server→port mapping from settings.

    Args:
        settings: Settings dictionary from parse_mcp_servers()

    Returns:
        dict: Mapping of server_name → port (None if port not found)
    """
    port_map = {}
    mcp_servers = settings.get('mcpServers', {})

    for server_name, config in mcp_servers.items():
        port = None
        args = config.get('args', [])

        # Look for SSE URL in args (format: http://localhost:PORT/sse)
        for i, arg in enumerate(args):
            if isinstance(arg, str) and 'http://localhost:' in arg and '/sse' in arg:
                try:
                    # Extract port from URL
                    url_part = arg.split('http://localhost:')[1]
                    port_str = url_part.split('/')[0]
                    port = int(port_str)
                    break
                except (IndexError, ValueError) as e:
                    logger.warning(f"Failed to extract port from {arg}: {e}")

        port_map[server_name] = port

    return port_map


def detect_port_conflicts(port_map: Dict[str, Optional[int]]) -> List[Dict[str, Any]]:
    """
    Find duplicate port assignments.

    Args:
        port_map: Mapping of server_name → port

    Returns:
        list: List of conflicts, each with port and list of servers using it
    """
    port_to_servers = defaultdict(list)

    for server_name, port in port_map.items():
        if port is not None:
            port_to_servers[port].append(server_name)

    conflicts = []
    for port, servers in port_to_servers.items():
        if len(servers) > 1:
            conflicts.append({
                "port": port,
                "servers": servers,
                "count": len(servers)
            })

    return conflicts


def detect_port_gaps(port_map: Dict[str, Optional[int]]) -> List[int]:
    """
    Find gaps in port sequence.

    Args:
        port_map: Mapping of server_name → port

    Returns:
        list: List of unused ports in the expected range
    """
    used_ports = set(port for port in port_map.values() if port is not None)
    all_ports = set(range(PORT_RANGE_MIN, PORT_RANGE_MAX + 1))
    gaps = sorted(list(all_ports - used_ports))

    return gaps


def get_transport_type(config: dict) -> str:
    """
    Detect transport type from server configuration.

    Args:
        config: Server configuration dictionary from mcp_servers.json

    Returns:
        Transport type: "http", "stdio", or "unknown"
    """
    if 'transport' in config:
        return 'http'  # SSE/HTTP transport (e.g., ref-mcp)
    elif 'command' in config:
        return 'stdio'  # stdio subprocess transport
    else:
        return 'unknown'


def detect_running_processes(server_name: str) -> List[Dict[str, Any]]:
    """
    Detect running processes for a given MCP server.

    Args:
        server_name: Name of the MCP server

    Returns:
        list: List of process info dicts with pid, command, started time
    """
    try:
        result = subprocess.run(
            ['ps', 'aux'],
            capture_output=True,
            text=True,
            timeout=2
        )

        processes = []
        for line in result.stdout.split('\n'):
            if server_name in line and 'grep' not in line:
                parts = line.split(None, 10)  # Split on whitespace, max 11 parts
                if len(parts) >= 11:
                    processes.append({
                        'pid': parts[1],
                        'command': parts[10],
                        'cpu_percent': parts[2],
                        'mem_percent': parts[3]
                    })

        return processes
    except Exception as e:
        logger.warning(f"Failed to detect processes for {server_name}: {e}")
        return []


def scan_http_port(port: int, timeout: float = 0.5) -> Optional[Dict[str, Any]]:
    """
    Check if an HTTP server is running on a specific port.

    Args:
        port: Port number to check
        timeout: Connection timeout in seconds

    Returns:
        dict: Server info if healthy, None if not responding
    """
    try:
        response = requests.get(
            f'http://localhost:{port}/health',
            timeout=timeout
        )
        if response.status_code in [200, 405]:  # 405 means endpoint exists but method not allowed
            return {
                'port': port,
                'status_code': response.status_code,
                'response': response.json() if response.status_code == 200 else None
            }
    except Exception:
        pass  # Port not responding

    return None


def check_venv_health(server_path: str) -> Optional[Dict[str, Any]]:
    """
    Check if a virtual environment exists and validate Python packages.

    Args:
        server_path: Path to the server directory

    Returns:
        dict: venv health info or None if no venv
    """
    venv_path = Path(server_path) / 'venv'
    if not venv_path.exists():
        return None

    try:
        python_path = venv_path / 'bin' / 'python'
        if not python_path.exists():
            return {'status': 'broken', 'error': 'python executable not found'}

        # Get Python version
        result = subprocess.run(
            [str(python_path), '--version'],
            capture_output=True,
            text=True,
            timeout=2
        )
        python_version = result.stdout.strip() if result.returncode == 0 else 'unknown'

        # Get installed packages (sample - top 5)
        result = subprocess.run(
            [str(python_path), '-m', 'pip', 'list', '--format=json'],
            capture_output=True,
            text=True,
            timeout=5
        )

        packages = []
        if result.returncode == 0:
            try:
                all_packages = json.loads(result.stdout)
                packages = [f"{p['name']}=={p['version']}" for p in all_packages[:5]]
            except json.JSONDecodeError:
                pass

        return {
            'status': 'healthy',
            'python_version': python_version,
            'venv_path': str(venv_path),
            'sample_packages': packages
        }

    except Exception as e:
        return {'status': 'error', 'error': str(e)}


async def check_stdio_server(server_name: str, config: dict, timeout: int = 5) -> Dict[str, Any]:
    """
    Test stdio server by spawning subprocess and checking if it starts.

    Per MCP specification, stdio servers communicate via stdin/stdout using
    newline-delimited JSON-RPC messages. We test by:
    1. Spawning the subprocess
    2. Sending an initialize request
    3. Waiting for a valid response or timeout

    For servers using uvx (which may need to build packages), we allow extra
    startup time and consider a process that's still running as "online".

    Enhanced Diagnostics (v2):
    - Detects running processes for the server
    - Scans for alternative HTTP/SSE servers on ports 5555-5582
    - Validates venv health if available
    - Provides comprehensive status including alternatives

    Args:
        server_name: Name of the server
        config: Server configuration with 'command' and 'args'
        timeout: Timeout in seconds for startup check

    Returns:
        dict: Status information with keys:
            - name: server name
            - transport: "stdio"
            - status: "online" | "offline" | "partial" | "error"
            - response_time_ms: startup time in milliseconds (if successful)
            - error: error message (if failed)
            - stderr: stderr output from failed process
            - running_processes: list of detected running processes
            - alternative_transports: list of working HTTP/SSE servers
            - venv_health: venv validation if exists
    """
    command = config.get('command')
    args = config.get('args', [])

    # Gather enhanced diagnostics
    running_processes = detect_running_processes(server_name)

    # Scan for HTTP servers on standard MCP port range (5555-5582)
    alternative_transports = []
    for port in range(5555, 5583):
        http_server = scan_http_port(port)
        if http_server and http_server.get('response'):
            # Check if this server matches our server_name
            server_info = http_server['response']
            if isinstance(server_info, dict) and server_info.get('server') == server_name:
                alternative_transports.append({
                    'type': 'http',
                    'port': port,
                    'status': 'online',
                    'health': http_server['response']
                })

    # Check for venv if using path-based command
    venv_health = None
    if args and '--from' in args:
        try:
            from_idx = args.index('--from')
            if from_idx + 1 < len(args):
                server_path = args[from_idx + 1]
                venv_health = check_venv_health(server_path)
        except (ValueError, IndexError):
            pass

    if not command:
        return {
            "name": server_name,
            "transport": "stdio",
            "status": "error",
            "error": "no command specified",
            "running_processes": running_processes,
            "alternative_transports": alternative_transports,
            "venv_health": venv_health
        }

    proc = None
    try:
        start_time = datetime.now()

        # Get environment - inherit current environment plus any server-specific env
        env = os.environ.copy()

        # Ensure HOME is set (needed by some tools)
        if 'HOME' not in env:
            env['HOME'] = str(Path.home())

        # Ensure PATH includes common binary locations
        path_entries = env.get('PATH', '').split(':')
        required_paths = [
            str(Path.home() / '.local' / 'bin'),  # User local binaries (uvx location)
            '/usr/local/bin',
            '/usr/bin',
        ]
        for path in required_paths:
            if path not in path_entries:
                path_entries.insert(0, path)
        env['PATH'] = ':'.join(path_entries)

        if 'env' in config:
            env.update(config['env'])

        # Spawn subprocess with pipes for stdin/stdout
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )

        # Give the process a minimal moment to start up
        # For faster health checks, use very short delay
        startup_delay = 0.05  # 50ms is enough to detect immediate crashes
        await asyncio.sleep(startup_delay)

        # Check if process crashed immediately (fail fast)
        if proc.returncode is not None:
            stderr_data = await proc.stderr.read()

            # Determine overall status
            overall_status = "partial" if alternative_transports else "offline"

            result = {
                "name": server_name,
                "transport": "stdio",
                "status": overall_status,
                "error": f"process exited immediately with code {proc.returncode}",
                "stderr": stderr_data.decode('utf-8', errors='replace')[:500]
            }

            # Add enhanced diagnostics
            if running_processes:
                result['running_processes'] = running_processes
            if alternative_transports:
                result['alternative_transports'] = alternative_transports
            if venv_health:
                result['venv_health'] = venv_health

            return result

        # Send MCP initialize request (JSON-RPC 2.0)
        init_request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "diagnostic-mcp-health-check",
                    "version": "1.0.0"
                }
            }
        }) + "\n"

        try:
            # Write initialize request
            proc.stdin.write(init_request.encode('utf-8'))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # Process died before we could write
            if proc.returncode is not None:
                stderr_data = await proc.stderr.read()

                # Determine overall status
                overall_status = "partial" if alternative_transports else "offline"

                result = {
                    "name": server_name,
                    "transport": "stdio",
                    "status": overall_status,
                    "error": f"process exited with code {proc.returncode} (broken pipe)",
                    "stderr": stderr_data.decode('utf-8', errors='replace')[:500]
                }

                # Add enhanced diagnostics
                if running_processes:
                    result['running_processes'] = running_processes
                if alternative_transports:
                    result['alternative_transports'] = alternative_transports
                if venv_health:
                    result['venv_health'] = venv_health

                return result

        # Wait for response with timeout
        try:
            response_line = await asyncio.wait_for(
                proc.stdout.readline(),
                timeout=timeout
            )
            end_time = datetime.now()
            response_time_ms = (end_time - start_time).total_seconds() * 1000

            if response_line:
                # Try to parse as JSON-RPC response
                try:
                    response = json.loads(response_line.decode('utf-8'))
                    if 'result' in response or 'error' in response:
                        # Valid JSON-RPC response - server is working
                        return {
                            "name": server_name,
                            "transport": "stdio",
                            "status": "online",
                            "response_time_ms": round(response_time_ms, 2)
                        }
                    else:
                        return {
                            "name": server_name,
                            "transport": "stdio",
                            "status": "online",
                            "response_time_ms": round(response_time_ms, 2),
                            "note": "non-standard response"
                        }
                except json.JSONDecodeError:
                    # Got output but not valid JSON - still consider online
                    return {
                        "name": server_name,
                        "transport": "stdio",
                        "status": "online",
                        "response_time_ms": round(response_time_ms, 2),
                        "note": "non-json response"
                    }
            else:
                # Empty response - check if process is still alive
                if proc.returncode is None:
                    end_time = datetime.now()
                    response_time_ms = (end_time - start_time).total_seconds() * 1000
                    return {
                        "name": server_name,
                        "transport": "stdio",
                        "status": "online",
                        "response_time_ms": round(response_time_ms, 2),
                        "note": "process running (no immediate response)"
                    }
                else:
                    stderr_data = await proc.stderr.read()

                    # Determine overall status
                    overall_status = "partial" if alternative_transports else "offline"

                    result = {
                        "name": server_name,
                        "transport": "stdio",
                        "status": overall_status,
                        "error": f"process exited with code {proc.returncode} (empty response)",
                        "stderr": stderr_data.decode('utf-8', errors='replace')[:500]
                    }

                    # Add enhanced diagnostics
                    if running_processes:
                        result['running_processes'] = running_processes
                    if alternative_transports:
                        result['alternative_transports'] = alternative_transports
                    if venv_health:
                        result['venv_health'] = venv_health

                    return result

        except asyncio.TimeoutError:
            end_time = datetime.now()
            response_time_ms = (end_time - start_time).total_seconds() * 1000

            # Timeout - check if process is still running
            # A running process after timeout is considered online (just slow)
            if proc.returncode is None:
                return {
                    "name": server_name,
                    "transport": "stdio",
                    "status": "online",
                    "response_time_ms": round(response_time_ms, 2),
                    "note": "slow response (process running)"
                }
            else:
                stderr_data = await proc.stderr.read()

                # Determine overall status
                overall_status = "partial" if alternative_transports else "offline"

                result = {
                    "name": server_name,
                    "transport": "stdio",
                    "status": overall_status,
                    "error": f"process exited with code {proc.returncode} (timeout)",
                    "stderr": stderr_data.decode('utf-8', errors='replace')[:500]
                }

                # Add enhanced diagnostics
                if running_processes:
                    result['running_processes'] = running_processes
                if alternative_transports:
                    result['alternative_transports'] = alternative_transports
                if venv_health:
                    result['venv_health'] = venv_health

                return result

    except FileNotFoundError:
        # Command not found - but check for alternatives
        overall_status = "partial" if alternative_transports else "error"

        result = {
            "name": server_name,
            "transport": "stdio",
            "status": overall_status,
            "error": f"command not found: {command}"
        }

        # Add enhanced diagnostics
        if running_processes:
            result['running_processes'] = running_processes
        if alternative_transports:
            result['alternative_transports'] = alternative_transports
        if venv_health:
            result['venv_health'] = venv_health

        return result

    except PermissionError:
        # Permission denied - but check for alternatives
        overall_status = "partial" if alternative_transports else "error"

        result = {
            "name": server_name,
            "transport": "stdio",
            "status": overall_status,
            "error": f"permission denied: {command}"
        }

        # Add enhanced diagnostics
        if running_processes:
            result['running_processes'] = running_processes
        if alternative_transports:
            result['alternative_transports'] = alternative_transports
        if venv_health:
            result['venv_health'] = venv_health

        return result

    except Exception as e:
        # General exception - but check for alternatives
        overall_status = "partial" if alternative_transports else "error"

        result = {
            "name": server_name,
            "transport": "stdio",
            "status": overall_status,
            "error": str(e)
        }

        # Add enhanced diagnostics
        if running_processes:
            result['running_processes'] = running_processes
        if alternative_transports:
            result['alternative_transports'] = alternative_transports
        if venv_health:
            result['venv_health'] = venv_health

        return result
    finally:
        # Clean up subprocess - use aggressive cleanup for faster health checks
        if proc is not None:
            try:
                # Try graceful termination first
                proc.terminate()
                try:
                    # Wait only 0.1s for graceful shutdown
                    await asyncio.wait_for(proc.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    # Force kill if not responding
                    try:
                        proc.kill()
                        await asyncio.wait_for(proc.wait(), timeout=0.1)
                    except (asyncio.TimeoutError, ProcessLookupError):
                        pass  # Process is gone, ignore
            except ProcessLookupError:
                pass  # Process already dead


async def check_http_server(server_name: str, config: dict, timeout: int = 5) -> Dict[str, Any]:
    """
    Test HTTP/SSE server by making an HTTP request to its endpoint.

    MCP HTTP servers may respond differently to health checks:
    - Some accept GET and return 200
    - Some only accept POST (return 405 for GET but still "reachable")
    - Some may return 400/401 but are still running

    We consider a server "online" if we can reach it (any HTTP response).
    Only connection failures or timeouts indicate the server is truly offline.

    Args:
        server_name: Name of the server
        config: Server configuration with 'transport.url'
        timeout: Timeout in seconds

    Returns:
        dict: Status information
    """
    transport = config.get('transport', {})
    url = transport.get('url', '')

    if not url:
        return {
            "name": server_name,
            "transport": "http",
            "status": "error",
            "error": "no URL specified"
        }

    try:
        start_time = datetime.now()
        response = await asyncio.to_thread(
            requests.get,
            url,
            timeout=timeout,
            allow_redirects=False
        )
        end_time = datetime.now()
        response_time_ms = (end_time - start_time).total_seconds() * 1000

        # Any HTTP response means the server is reachable/online
        # Even 4xx/5xx errors indicate the server is running
        # Only connection failures indicate truly offline
        if response.status_code in [200, 201, 202, 204, 101]:
            return {
                "name": server_name,
                "transport": "http",
                "status": "online",
                "response_time_ms": round(response_time_ms, 2),
                "http_status": response.status_code
            }
        elif response.status_code in [400, 401, 403, 404, 405, 500, 502, 503]:
            # Server is reachable but returned an error
            # This is still "online" - the server is running
            return {
                "name": server_name,
                "transport": "http",
                "status": "online",
                "response_time_ms": round(response_time_ms, 2),
                "http_status": response.status_code,
                "note": f"reachable but returned HTTP {response.status_code}"
            }
        else:
            return {
                "name": server_name,
                "transport": "http",
                "status": "online",
                "response_time_ms": round(response_time_ms, 2),
                "http_status": response.status_code,
                "note": f"unexpected status code"
            }

    except requests.exceptions.Timeout:
        return {
            "name": server_name,
            "transport": "http",
            "status": "offline",
            "error": "timeout"
        }
    except requests.exceptions.ConnectionError:
        return {
            "name": server_name,
            "transport": "http",
            "status": "offline",
            "error": "connection_refused"
        }
    except Exception as e:
        return {
            "name": server_name,
            "transport": "http",
            "status": "error",
            "error": str(e)
        }


async def check_sse_endpoint(port: int, server_name: str) -> Dict[str, Any]:
    """
    Test SSE endpoint at http://localhost:{port}/sse.

    NOTE: This function is kept for backward compatibility with SSE-port based servers.
    For new transport-aware checking, use check_stdio_server() or check_http_server().

    Args:
        port: Port number to check
        server_name: Name of the server (for logging)

    Returns:
        dict: Status information with keys:
            - name: server name
            - port: port number
            - status: "online" | "offline" | "error"
            - response_time_ms: response time in milliseconds (if successful)
            - error: error message (if failed)
    """
    url = f"http://localhost:{port}/sse"

    try:
        start_time = datetime.now()
        response = await asyncio.to_thread(
            requests.get,
            url,
            timeout=SSE_TIMEOUT,
            allow_redirects=False
        )
        end_time = datetime.now()

        response_time_ms = (end_time - start_time).total_seconds() * 1000

        # SSE endpoints typically return 200 or start streaming
        if response.status_code in [200, 101]:
            return {
                "name": server_name,
                "port": port,
                "status": "online",
                "response_time_ms": round(response_time_ms, 2),
                "http_status": response.status_code
            }
        else:
            return {
                "name": server_name,
                "port": port,
                "status": "error",
                "error": f"HTTP {response.status_code}",
                "response_time_ms": round(response_time_ms, 2)
            }

    except requests.exceptions.Timeout:
        return {
            "name": server_name,
            "port": port,
            "status": "offline",
            "error": "timeout"
        }
    except requests.exceptions.ConnectionError:
        return {
            "name": server_name,
            "port": port,
            "status": "offline",
            "error": "connection_refused"
        }
    except Exception as e:
        return {
            "name": server_name,
            "port": port,
            "status": "error",
            "error": str(e)
        }


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """List all available tools."""
    return [
        # Port Consistency Check
        types.Tool(
            name="check_port_consistency",
            description="Check MCP server port assignments for conflicts, gaps, and consistency",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),

        # Health Check All
        types.Tool(
            name="check_all_health",
            description="Test health of all MCP servers (stdio + HTTP transports). Stdio servers are tested by spawning subprocess and sending MCP initialize request. HTTP servers are tested via HTTP request.",
            inputSchema={
                "type": "object",
                "properties": {
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds for each health check (default: 5)",
                        "default": 5
                    },
                    "critical_only": {
                        "type": "boolean",
                        "description": "Only check critical servers (diagnostic, knowledge, github, docker, system-ops) for faster checks (default: false)",
                        "default": False
                    }
                },
                "required": []
            }
        ),

        # Configuration Check
        types.Tool(
            name="check_configurations",
            description="Validate MCP server configurations in mcp_servers.json",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),

        # Tool Availability Check
        types.Tool(
            name="check_tool_availability",
            description="Check tool availability and naming conflicts across servers",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),

        # Full Diagnostic
        types.Tool(
            name="run_full_diagnostic",
            description="Run comprehensive diagnostic check (all checks combined)",
            inputSchema={
                "type": "object",
                "properties": {
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds for health checks (default: 5)",
                        "default": 5
                    }
                },
                "required": []
            }
        ),

        # Export Configuration
        types.Tool(
            name="export_configuration",
            description="Export MCP server configurations for backup, migration, and documentation. Supports JSON, YAML, and Markdown formats.",
            inputSchema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["json", "yaml", "markdown"],
                        "description": "Export format (default: json)",
                        "default": "json"
                    },
                    "servers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of server names to export (omit for all servers)"
                    },
                    "include_health": {
                        "type": "boolean",
                        "description": "Include health check results in export (default: false)",
                        "default": False
                    },
                    "include_tools": {
                        "type": "boolean",
                        "description": "Include tool availability information (default: false)",
                        "default": False
                    }
                },
                "required": []
            }
        ),

        # Multi-Transport Testing
        types.Tool(
            name="test_multi_transport",
            description="Test MCP servers across multiple transport types (stdio, HTTP/SSE) to detect dual-transport configurations, compatibility issues, and configuration inconsistencies.",
            inputSchema={
                "type": "object",
                "properties": {
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds for each transport test (default: 5)",
                        "default": 5
                    },
                    "servers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of server names to test (omit for all servers)"
                    }
                },
                "required": []
            }
        ),

        # Probe Health Checks
        types.Tool(
            name="check_readiness_probe",
            description="Check readiness probe status for diagnostic-mcp HTTP server. Returns UP if server is ready to accept traffic, DOWN if experiencing too many rejections or still starting up.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),

        types.Tool(
            name="check_liveness_probe",
            description="Check liveness probe status for diagnostic-mcp HTTP server. Returns UP if server is alive and responding, DOWN if critical failure detected.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),

        types.Tool(
            name="get_probe_status",
            description="Get comprehensive probe status for diagnostic-mcp HTTP server. Returns startup, liveness, and readiness probe states with overall health assessment.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),

        # Authentication Tools
        types.Tool(
            name="create_auth_token",
            description="Create a new session authentication token. Requires admin authentication. Returns token and expiration details.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ttl_hours": {
                        "type": "number",
                        "description": "Token TTL in hours (default: 24)",
                        "default": 24
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata to store with token (purpose, client info, etc.)",
                        "additionalProperties": True
                    }
                },
                "required": []
            }
        ),

        types.Tool(
            name="revoke_auth_token",
            description="Revoke an authentication token by ID. Requires admin authentication.",
            inputSchema={
                "type": "object",
                "properties": {
                    "token_id": {
                        "type": "string",
                        "description": "UUID of the token to revoke"
                    }
                },
                "required": ["token_id"]
            }
        ),

        types.Tool(
            name="list_active_tokens",
            description="List all active (non-expired, non-revoked) authentication tokens. Requires admin authentication. Does not return plaintext tokens.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),

        # Trend Analysis Tools
        types.Tool(
            name="analyze_health_trends",
            description="Analyze health trends over specified time window. Calculate uptime %, failure rate, response time trends, status changes, and degradation score.",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_window": {
                        "type": "string",
                        "description": "Time window (e.g., '1h', '24h', '7d', '30d')",
                        "default": "24h"
                    },
                    "server_filter": {
                        "type": "string",
                        "description": "Optional server name to filter by"
                    }
                },
                "required": []
            }
        ),

        types.Tool(
            name="get_server_history",
            description="Get historical health checks for a specific server. Returns uptime %, response time stats, and check history.",
            inputSchema={
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "description": "Name of the server"
                    },
                    "time_window": {
                        "type": "string",
                        "description": "Time window (e.g., '1h', '24h', '7d', '30d')",
                        "default": "24h"
                    }
                },
                "required": ["server_name"]
            }
        ),

        types.Tool(
            name="detect_degradations",
            description="Detect servers with declining uptime (degradations). Compares first half vs second half of time window.",
            inputSchema={
                "type": "object",
                "properties": {
                    "time_window": {
                        "type": "string",
                        "description": "Time window (e.g., '24h', '7d', '30d')",
                        "default": "24h"
                    },
                    "threshold": {
                        "type": "number",
                        "description": "Minimum uptime decline percentage to flag (default: 20.0)",
                        "default": 20.0
                    }
                },
                "required": []
            }
        ),

        types.Tool(
            name="compare_time_periods",
            description="Compare metrics between two time periods. Returns uptime, failure rate, and response time deltas.",
            inputSchema={
                "type": "object",
                "properties": {
                    "period1_start": {
                        "type": "string",
                        "description": "ISO timestamp for period 1 start"
                    },
                    "period1_end": {
                        "type": "string",
                        "description": "ISO timestamp for period 1 end"
                    },
                    "period2_start": {
                        "type": "string",
                        "description": "ISO timestamp for period 2 start"
                    },
                    "period2_end": {
                        "type": "string",
                        "description": "ISO timestamp for period 2 end"
                    }
                },
                "required": ["period1_start", "period1_end", "period2_start", "period2_end"]
            }
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[types.TextContent]:
    """Handle tool calls."""
    try:
        if name == "check_port_consistency":
            return await handle_check_port_consistency(arguments)
        elif name == "check_all_health":
            return await handle_check_all_health(arguments)
        elif name == "check_configurations":
            return await handle_check_configurations(arguments)
        elif name == "check_tool_availability":
            return await handle_check_tool_availability(arguments)
        elif name == "run_full_diagnostic":
            return await handle_run_full_diagnostic(arguments)
        elif name == "export_configuration":
            return await handle_export_configuration(arguments)
        elif name == "test_multi_transport":
            return await handle_test_multi_transport(arguments)
        elif name == "check_readiness_probe":
            return await handle_check_readiness_probe(arguments)
        elif name == "check_liveness_probe":
            return await handle_check_liveness_probe(arguments)
        elif name == "get_probe_status":
            return await handle_get_probe_status(arguments)
        elif name == "create_auth_token":
            return await handle_create_auth_token(arguments)
        elif name == "revoke_auth_token":
            return await handle_revoke_auth_token(arguments)
        elif name == "list_active_tokens":
            return await handle_list_active_tokens(arguments)
        elif name == "analyze_health_trends":
            return await handle_analyze_health_trends(arguments)
        elif name == "get_server_history":
            return await handle_get_server_history(arguments)
        elif name == "detect_degradations":
            return await handle_detect_degradations(arguments)
        elif name == "compare_time_periods":
            return await handle_compare_time_periods(arguments)
        else:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.INVALID_ARGUMENT,
                    f"Unknown tool: {name}"
                )
            )
    except Exception as e:
        logger.exception(f"Tool {name} failed")
        sentry_sdk.capture_exception(e)
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Tool execution failed: {str(e)}"
            )
        )


# Public API functions for CLI and HTTP server
# These wrap the handlers and return dict results instead of MCP TextContent

async def check_port_consistency() -> dict:
    """Public API: Check port consistency (returns dict)."""
    result = await handle_check_port_consistency({})
    return json.loads(result[0].text)


async def check_all_health(timeout: int = 5, critical_only: bool = False) -> dict:
    """
    Public API: Check all server health (returns dict).

    Args:
        timeout: Timeout per server in seconds
        critical_only: If True, only check critical servers for faster checks
    """
    result = await handle_check_all_health({"timeout": timeout, "critical_only": critical_only})
    return json.loads(result[0].text)


async def check_configurations() -> dict:
    """Public API: Check configurations (returns dict)."""
    result = await handle_check_configurations({})
    return json.loads(result[0].text)


async def check_tool_availability() -> dict:
    """Public API: Check tool availability (returns dict)."""
    result = await handle_check_tool_availability({})
    return json.loads(result[0].text)


# Handler functions (MCP protocol)

async def handle_check_port_consistency(arguments: dict) -> list[types.TextContent]:
    """Handle check_port_consistency tool."""
    try:
        settings = parse_mcp_servers()
        mcp_servers = settings.get('mcpServers', {})
        port_map = extract_port_map(settings)
        conflicts = detect_port_conflicts(port_map)

        # Categorize servers by transport type
        stdio_servers = []
        sse_servers = []
        sse_servers_without_ports = []

        for server_name, config in mcp_servers.items():
            command = config.get('command', '')
            args = config.get('args', [])

            if command == 'uvx' or (command == 'uv' and 'run' in args):
                # Stdio transport - doesn't need ports
                stdio_servers.append(server_name)
            elif command == 'npx' and '--sse' in args:
                # SSE transport - needs ports
                sse_servers.append(server_name)
                if port_map.get(server_name) is None:
                    sse_servers_without_ports.append(server_name)
            else:
                # Unknown - check if it has a port
                if port_map.get(server_name) is not None:
                    sse_servers.append(server_name)
                else:
                    stdio_servers.append(server_name)

        # Only calculate gaps if we have SSE servers
        gaps = detect_port_gaps(port_map) if sse_servers else []

        # Find ports outside expected range (only for SSE servers)
        ports_out_of_range = [
            {"server": server, "port": port}
            for server, port in port_map.items()
            if port is not None and (port < PORT_RANGE_MIN or port > PORT_RANGE_MAX)
        ]

        # Real issues: only conflicts and SSE servers missing ports
        real_issues = len(conflicts) + len(sse_servers_without_ports) + len(ports_out_of_range)

        result = {
            "port_map": port_map,
            "conflicts": conflicts,
            "transport_summary": {
                "stdio_servers": len(stdio_servers),
                "sse_servers": len(sse_servers),
                "stdio_server_list": stdio_servers,
                "sse_server_list": sse_servers
            },
            "sse_servers_without_ports": sse_servers_without_ports,
            "ports_out_of_range": ports_out_of_range,
            "port_range": {
                "min": PORT_RANGE_MIN,
                "max": PORT_RANGE_MAX
            },
            "summary": {
                "total_servers": len(port_map),
                "stdio_servers": len(stdio_servers),
                "sse_servers": len(sse_servers),
                "servers_with_ports": len([p for p in port_map.values() if p is not None]),
                "conflicts_count": len(conflicts),
                "issues_found": real_issues,
                "note": "stdio servers don't require ports - only SSE missing ports are issues"
            }
        }

        logger.info(f"Port consistency check: {real_issues} issues found ({len(stdio_servers)} stdio, {len(sse_servers)} SSE)")

        return format_response(
            ResponseEnvelope.success(
                "Port consistency check completed",
                data=result
            )
        )

    except Exception as e:
        logger.error(f"Failed to check port consistency: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to check port consistency: {str(e)}"
            )
        )


async def handle_check_all_health(arguments: dict) -> list[types.TextContent]:
    """
    Handle check_all_health tool.

    Now supports both stdio and HTTP transport types per MCP specification:
    - stdio: Tests by spawning subprocess and sending MCP initialize request
    - http: Tests by making HTTP request to transport URL

    Args:
        arguments: Tool arguments including:
            - timeout: Timeout per server (default: 5)
            - critical_only: Only check critical servers (default: False)
    """
    global SSE_TIMEOUT

    timeout = arguments.get("timeout", 5)
    critical_only = arguments.get("critical_only", False)
    original_timeout = SSE_TIMEOUT
    SSE_TIMEOUT = timeout

    # Define critical servers (infrastructure essential for operation)
    CRITICAL_SERVERS = {
        "diagnostic-mcp",  # Self
        "knowledge-mcp",   # KB access
        "github-mcp",      # Git operations
        "docker-mcp",      # Container management
        "system-ops-mcp",  # System operations
    }

    try:
        settings = parse_mcp_servers()
        all_mcp_servers = settings.get('mcpServers', {})

        # Filter to critical servers if requested
        if critical_only:
            mcp_servers = {
                name: config
                for name, config in all_mcp_servers.items()
                if name in CRITICAL_SERVERS
            }
            logger.info(f"Quick mode: checking {len(mcp_servers)}/{len(all_mcp_servers)} critical servers")
        else:
            mcp_servers = all_mcp_servers

        # Build server check info
        server_checks = []
        server_transport_map = {}  # Track transport type for each server

        for server_name, config in mcp_servers.items():
            transport_type = get_transport_type(config)
            server_transport_map[server_name] = transport_type
            server_checks.append((server_name, config, transport_type))

        # Limit concurrent subprocess spawns to reduce overhead
        # HTTP checks are fast, stdio checks are slow (subprocess spawn)
        # Increase concurrency for faster checks
        max_concurrent_stdio = 16  # Limit concurrent stdio spawns (higher for faster checks)

        async def check_server_with_semaphore(server_name, config, transport_type, semaphore):
            """Check server with semaphore to limit concurrent stdio spawns."""
            if transport_type == 'stdio':
                async with semaphore:
                    return await check_stdio_server(server_name, config, timeout)
            elif transport_type == 'http':
                return await check_http_server(server_name, config, timeout)
            else:
                return {
                    "name": server_name,
                    "transport": "unknown",
                    "status": "error",
                    "error": "unknown transport type"
                }

        # Create semaphore for stdio checks
        stdio_semaphore = asyncio.Semaphore(max_concurrent_stdio)

        # Create tasks with semaphore
        tasks = [
            check_server_with_semaphore(name, config, transport, stdio_semaphore)
            for name, config, transport in server_checks
        ]

        # Check all servers in parallel (with stdio semaphore limiting concurrency)
        # Use return_exceptions to handle individual check failures
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to error results
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                server_name = server_checks[i][0]
                final_results.append({
                    "name": server_name,
                    "transport": server_transport_map[server_name],
                    "status": "error",
                    "error": str(result)
                })
            else:
                final_results.append(result)

        results = final_results

        # Categorize results by status
        online = [r for r in results if r["status"] == "online"]
        offline = [r for r in results if r["status"] == "offline"]
        error = [r for r in results if r["status"] == "error"]

        # Categorize by transport type
        stdio_online = [r for r in online if r.get("transport") == "stdio"]
        stdio_offline = [r for r in offline if r.get("transport") == "stdio"]
        stdio_error = [r for r in error if r.get("transport") == "stdio"]

        http_online = [r for r in online if r.get("transport") == "http"]
        http_offline = [r for r in offline if r.get("transport") == "http"]
        http_error = [r for r in error if r.get("transport") == "http"]

        result = {
            # Overall summary
            "servers_online": len(online),
            "servers_offline": len(offline),
            "servers_error": len(error),
            "total_checked": len(results),

            # Detailed by status
            "online_servers": online,
            "offline_servers": offline,
            "error_servers": error,

            # Transport type breakdown
            "transport_summary": {
                "stdio": {
                    "total": len(stdio_online) + len(stdio_offline) + len(stdio_error),
                    "online": len(stdio_online),
                    "offline": len(stdio_offline),
                    "error": len(stdio_error)
                },
                "http": {
                    "total": len(http_online) + len(http_offline) + len(http_error),
                    "online": len(http_online),
                    "offline": len(http_offline),
                    "error": len(http_error)
                }
            },

            # Backward compatibility - no longer skipping servers
            "servers_skipped": []
        }

        logger.info(
            f"Health check: {len(online)}/{len(results)} online "
            f"(stdio: {len(stdio_online)}/{len(stdio_online)+len(stdio_offline)+len(stdio_error)}, "
            f"http: {len(http_online)}/{len(http_online)+len(http_offline)+len(http_error)})"
        )

        return format_response(
            ResponseEnvelope.success(
                f"Health check completed: {len(online)}/{len(results)} servers online",
                data=result
            )
        )

    except Exception as e:
        logger.error(f"Failed to check health: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to check health: {str(e)}"
            )
        )
    finally:
        SSE_TIMEOUT = original_timeout


async def handle_check_configurations(arguments: dict) -> list[types.TextContent]:
    """Handle check_configurations tool."""
    try:
        settings = parse_mcp_servers()
        mcp_servers = settings.get('mcpServers', {})

        issues = []
        consistent_count = 0
        transport_stats = {"stdio": 0, "sse": 0, "http": 0, "unknown": 0}

        for server_name, config in mcp_servers.items():
            server_issues = []
            transport_type = "unknown"

            # Check for command field OR transport field (remote HTTP)
            if 'transport' in config:
                # Remote HTTP transport (e.g., ref.tools)
                transport_type = "http"
                transport_config = config.get('transport', {})
                if transport_config.get('type') != 'http':
                    server_issues.append(f"http: unknown transport type '{transport_config.get('type')}'")
                if 'url' not in transport_config:
                    server_issues.append("http: missing 'url' in transport config")
            elif 'command' not in config:
                server_issues.append("missing 'command' field")
            else:
                command = config['command']
                args = config.get('args', [])

                # Detect transport type - stdio or SSE
                if command == 'uvx':
                    # Stdio transport pattern: uvx --from /path server-name
                    transport_type = "stdio"
                    if '--from' not in args:
                        server_issues.append("stdio: missing '--from' in args")
                    else:
                        # Check that server path exists
                        from_idx = args.index('--from')
                        if from_idx + 1 < len(args):
                            server_path = args[from_idx + 1]
                            if not Path(server_path).exists():
                                server_issues.append(f"stdio: server path not found: {server_path}")
                        else:
                            server_issues.append("stdio: missing path after '--from'")

                elif command == 'npx':
                    # Check if this is SSE pattern or another stdio pattern
                    if '--sse' in args:
                        # SSE transport pattern: npx -y supergateway --sse http://...
                        transport_type = "sse"
                        if 'supergateway' not in args:
                            server_issues.append("sse: not using supergateway")
                    else:
                        # Direct npx stdio pattern (less common)
                        transport_type = "stdio"

                elif command == 'uv':
                    # Alternative stdio pattern: uv run --directory /path server
                    transport_type = "stdio"
                    if 'run' not in args:
                        server_issues.append("stdio: missing 'run' in uv args")

                elif command in ['python', 'python3', 'node']:
                    # Direct interpreter invocation - stdio pattern
                    transport_type = "stdio"

                else:
                    server_issues.append(f"unknown command: '{command}'")

            # Check for args field (not required for HTTP transport)
            if 'args' not in config and transport_type != "http":
                server_issues.append("missing 'args' field")

            # Check for description
            if 'description' not in config:
                server_issues.append("missing 'description' field")
            elif not config['description']:
                server_issues.append("empty description")

            # Update stats
            transport_stats[transport_type] = transport_stats.get(transport_type, 0) + 1

            if server_issues:
                issues.append({
                    "server": server_name,
                    "transport": transport_type,
                    "issues": server_issues
                })
            else:
                consistent_count += 1

        result = {
            "total_servers": len(mcp_servers),
            "consistent_format": consistent_count,
            "servers_with_issues": len(issues),
            "transport_stats": transport_stats,
            "issues": issues
        }

        logger.info(f"Configuration check: {consistent_count}/{len(mcp_servers)} servers configured correctly")

        return format_response(
            ResponseEnvelope.success(
                f"Configuration check completed: {consistent_count}/{len(mcp_servers)} servers configured correctly",
                data=result
            )
        )

    except Exception as e:
        logger.error(f"Failed to check configurations: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to check configurations: {str(e)}"
            )
        )


async def handle_check_tool_availability(arguments: dict) -> list[types.TextContent]:
    """
    Handle check_tool_availability tool.

    Queries MCP Index database to verify actual tool loading vs configuration.
    Returns tool counts per server, naming conflicts, and health status.
    """
    try:
        # Check if Supabase is available
        if not supabase:
            logger.warning("Supabase client not available - cannot query MCP Index")
            settings = parse_mcp_servers()
            mcp_servers = settings.get('mcpServers', {})

            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.INVALID_INPUT,
                    "Supabase connection not available - cannot verify tool loading",
                    data={
                        "total_servers_configured": len(mcp_servers),
                        "servers": list(mcp_servers.keys())
                    }
                )
            )

        # Get configured servers from mcp_servers.json
        settings = parse_mcp_servers()
        mcp_servers = settings.get('mcpServers', {})
        configured_servers = set(mcp_servers.keys())

        logger.info(f"Querying MCP Index database for tool availability...")

        # Query mcp_servers table to get active servers
        servers_result = supabase.table("mcp_servers")\
            .select("*")\
            .eq("status", "active")\
            .execute()

        active_servers = {s["server_id"]: s for s in servers_result.data}
        active_server_ids = set(active_servers.keys())

        # Query mcp_tools table to get all loaded tools
        tools_result = supabase.table("mcp_tools")\
            .select("*")\
            .execute()

        all_tools = tools_result.data

        # Build tools per server mapping
        tools_per_server = defaultdict(list)
        for tool in all_tools:
            tools_per_server[tool["server_id"]].append(tool["tool_name"])

        # Calculate statistics
        total_tools_loaded = len(all_tools)
        servers_with_tools = len([s for s in active_server_ids if len(tools_per_server[s]) > 0])
        servers_without_tools = [s for s in configured_servers if s not in active_server_ids or len(tools_per_server[s]) == 0]
        servers_not_configured = active_server_ids - configured_servers

        # Detect naming conflicts (same tool_name across different servers)
        tool_name_to_servers = defaultdict(list)
        for tool in all_tools:
            tool_name_to_servers[tool["tool_name"]].append(tool["server_id"])

        naming_conflicts = [
            {
                "tool_name": tool_name,
                "servers": servers,
                "count": len(servers)
            }
            for tool_name, servers in tool_name_to_servers.items()
            if len(servers) > 1
        ]

        # Build detailed tools per server
        tools_per_server_details = {}
        for server_id in active_server_ids:
            tools = tools_per_server[server_id]
            tools_per_server_details[server_id] = {
                "tool_count": len(tools),
                "tools": sorted(tools),
                "configured": server_id in configured_servers
            }

        # Determine health status
        health = "healthy"
        if len(servers_without_tools) > 0:
            health = "warning"
        if len(naming_conflicts) > 5:
            health = "error"

        # Build result
        result = {
            "total_servers_configured": len(configured_servers),
            "total_servers_with_tools": servers_with_tools,
            "total_tools_loaded": total_tools_loaded,
            "servers_without_tools": servers_without_tools,
            "servers_not_configured": list(servers_not_configured),
            "tools_per_server": tools_per_server_details,
            "naming_conflicts": naming_conflicts,
            "summary": {
                "health": health,
                "conflicts_found": len(naming_conflicts),
                "servers_not_loaded": len(servers_without_tools),
                "unconfigured_servers": len(servers_not_configured)
            }
        }

        logger.info(
            f"Tool availability check complete: {servers_with_tools} servers, "
            f"{total_tools_loaded} tools, {len(naming_conflicts)} conflicts"
        )

        return format_response(
            ResponseEnvelope.success(
                f"Verified {total_tools_loaded} tools across {servers_with_tools} servers",
                data=result
            )
        )

    except Exception as e:
        logger.error(f"Failed to check tool availability: {e}", exc_info=True)
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to check tool availability: {str(e)}"
            )
        )


async def handle_run_full_diagnostic(arguments: dict) -> list[types.TextContent]:
    """Handle run_full_diagnostic tool."""
    try:
        logger.info("Running full diagnostic...")

        # Run all checks
        port_check = await handle_check_port_consistency({})
        health_check = await handle_check_all_health(arguments)
        config_check = await handle_check_configurations({})
        tool_check = await handle_check_tool_availability({})

        # Parse results
        port_result = json.loads(port_check[0].text)
        health_result = json.loads(health_check[0].text)
        config_result = json.loads(config_check[0].text)
        tool_result = json.loads(tool_check[0].text)

        # Count total issues
        total_issues = 0
        critical_issues = 0

        if port_result.get("ok"):
            port_data = port_result.get("data", {})
            port_summary = port_data.get("summary", {})
            total_issues += port_summary.get("issues_found", 0)
            critical_issues += port_summary.get("conflicts_count", 0)

        if health_result.get("ok"):
            health_data = health_result.get("data", {})
            offline_count = health_data.get("servers_offline", 0)
            error_count = health_data.get("servers_error", 0)
            total_issues += offline_count + error_count
            critical_issues += offline_count

        if config_result.get("ok"):
            config_data = config_result.get("data", {})
            total_issues += config_data.get("servers_with_issues", 0)

        # Build recommendations
        recommendations = []

        if port_result.get("ok") and port_result["data"]["summary"]["conflicts_count"] > 0:
            recommendations.append("CRITICAL: Resolve port conflicts immediately")

        if health_result.get("ok") and health_result["data"]["servers_offline"] > 0:
            recommendations.append("CRITICAL: Restart offline MCP servers")

        if config_result.get("ok") and config_result["data"]["servers_with_issues"] > 0:
            recommendations.append("Review and fix configuration issues in settings.json")

        if not recommendations:
            recommendations.append("All systems operational")

        result = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "summary": {
                "total_issues": total_issues,
                "critical_issues": critical_issues,
                "status": "critical" if critical_issues > 0 else ("warning" if total_issues > 0 else "healthy")
            },
            "port_check": port_result,
            "health_check": health_result,
            "config_check": config_result,
            "tool_check": tool_result,
            "recommendations": recommendations
        }

        logger.info(f"Full diagnostic completed: {total_issues} total issues, {critical_issues} critical")

        return format_response(
            ResponseEnvelope.success(
                f"Full diagnostic completed: {total_issues} issues found ({critical_issues} critical)",
                data=result
            )
        )

    except Exception as e:
        logger.error(f"Failed to run full diagnostic: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to run full diagnostic: {str(e)}"
            )
        )


async def handle_export_configuration(arguments: dict) -> list[types.TextContent]:
    """Handle export_configuration tool."""
    try:
        from diagnostic_mcp.config_export import (
            export_configurations,
            export_to_json,
            export_to_yaml,
            export_to_markdown
        )

        logger.info("Exporting MCP server configurations...")

        # Extract arguments
        format = arguments.get("format", "json")
        servers = arguments.get("servers")
        include_health = arguments.get("include_health", False)
        include_tools = arguments.get("include_tools", False)

        # Export configuration
        export_data = await export_configurations(
            format=format,
            servers=servers,
            include_health=include_health,
            include_tools=include_tools
        )

        # Check for errors
        if "error" in export_data:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    export_data["error"]
                )
            )

        # Convert to requested format
        if format == "json":
            content = export_to_json(export_data)
        elif format == "yaml":
            content = export_to_yaml(export_data)
        elif format == "markdown":
            content = export_to_markdown(export_data)
        else:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.INVALID_PARAMS,
                    f"Unsupported format: {format}"
                )
            )

        result = {
            "format": format,
            "total_servers": export_data.get("total_servers", 0),
            "timestamp": export_data.get("timestamp"),
            "content": content,
            "included_health": include_health,
            "included_tools": include_tools
        }

        if servers:
            result["filtered_servers"] = servers

        logger.info(f"Configuration exported: {result['total_servers']} servers in {format} format")

        return format_response(
            ResponseEnvelope.success(
                f"Configuration exported in {format} format ({result['total_servers']} servers)",
                data=result
            )
        )

    except Exception as e:
        logger.error(f"Failed to export configuration: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to export configuration: {str(e)}"
            )
        )


async def handle_test_multi_transport(arguments: dict) -> list[types.TextContent]:
    """Handle test_multi_transport tool."""
    try:
        from diagnostic_mcp.transport_testing import test_multi_transport

        logger.info("Starting multi-transport testing...")

        # Extract arguments
        timeout = arguments.get("timeout", 5)
        servers = arguments.get("servers")

        # Run multi-transport test
        result = await test_multi_transport(
            timeout=timeout,
            servers=servers
        )

        # Check for errors
        if not result.get("ok", False):
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    result.get("error", "Multi-transport test failed")
                )
            )

        summary = result.get("summary", {})
        logger.info(
            f"Multi-transport testing complete: {summary.get('dual_transport_count', 0)} dual-transport, "
            f"{summary.get('offline_count', 0)} offline servers"
        )

        return format_response(
            ResponseEnvelope.success(
                f"Tested {summary.get('total_servers', 0)} servers across multiple transports",
                data=result
            )
        )

    except Exception as e:
        logger.error(f"Failed to run multi-transport test: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to run multi-transport test: {str(e)}"
            )
        )


async def handle_check_readiness_probe(arguments: dict) -> list[types.TextContent]:
    """
    Handle check_readiness_probe tool.

    Queries the HTTP server's readiness probe endpoint.
    """
    try:
        logger.info("Checking readiness probe status...")

        # Query the HTTP server's readiness endpoint
        # Default to localhost:5555 (standard diagnostic-mcp HTTP port)
        http_port = int(os.environ.get("MCP_HTTP_PORT", "5555"))
        url = f"http://localhost:{http_port}/health?ready"

        try:
            response = await asyncio.to_thread(
                requests.get,
                url,
                timeout=5
            )

            probe_data = response.json()

            result = {
                "probe_type": "readiness",
                "http_status": response.status_code,
                "probe_status": probe_data.get("status"),
                "degraded": probe_data.get("degraded", False),
                "timestamp": probe_data.get("timestamp"),
                "metrics": probe_data.get("metrics", {}),
                "uptime_seconds": probe_data.get("uptime_seconds"),
                "message": probe_data.get("message"),
                "reason": probe_data.get("reason")
            }

            logger.info(f"Readiness probe: {probe_data.get('status')} (HTTP {response.status_code})")

            return format_response(
                ResponseEnvelope.success(
                    f"Readiness probe: {probe_data.get('status')}",
                    data=result
                )
            )

        except requests.exceptions.ConnectionError:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    f"Cannot connect to HTTP server at {url}. Is the HTTP server running?",
                    data={"url": url}
                )
            )
        except requests.exceptions.Timeout:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    f"Timeout querying HTTP server at {url}",
                    data={"url": url}
                )
            )

    except Exception as e:
        logger.error(f"Failed to check readiness probe: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to check readiness probe: {str(e)}"
            )
        )


async def handle_check_liveness_probe(arguments: dict) -> list[types.TextContent]:
    """
    Handle check_liveness_probe tool.

    Queries the HTTP server's liveness probe endpoint.
    """
    try:
        logger.info("Checking liveness probe status...")

        # Query the HTTP server's liveness endpoint
        http_port = int(os.environ.get("MCP_HTTP_PORT", "5555"))
        url = f"http://localhost:{http_port}/health?live"

        try:
            response = await asyncio.to_thread(
                requests.get,
                url,
                timeout=5
            )

            probe_data = response.json()

            result = {
                "probe_type": "liveness",
                "http_status": response.status_code,
                "probe_status": probe_data.get("status"),
                "timestamp": probe_data.get("timestamp"),
                "uptime_seconds": probe_data.get("uptime_seconds"),
                "last_health_check": probe_data.get("last_health_check"),
                "consecutive_failures": probe_data.get("consecutive_failures"),
                "reason": probe_data.get("reason"),
                "message": probe_data.get("message")
            }

            logger.info(f"Liveness probe: {probe_data.get('status')} (HTTP {response.status_code})")

            return format_response(
                ResponseEnvelope.success(
                    f"Liveness probe: {probe_data.get('status')}",
                    data=result
                )
            )

        except requests.exceptions.ConnectionError:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    f"Cannot connect to HTTP server at {url}. Is the HTTP server running?",
                    data={"url": url}
                )
            )
        except requests.exceptions.Timeout:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    f"Timeout querying HTTP server at {url}",
                    data={"url": url}
                )
            )

    except Exception as e:
        logger.error(f"Failed to check liveness probe: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to check liveness probe: {str(e)}"
            )
        )


async def handle_get_probe_status(arguments: dict) -> list[types.TextContent]:
    """
    Handle get_probe_status tool.

    Queries the HTTP server's comprehensive probe status endpoint.
    """
    try:
        logger.info("Getting comprehensive probe status...")

        # Query the HTTP server's probe status endpoint
        http_port = int(os.environ.get("MCP_HTTP_PORT", "5555"))
        url = f"http://localhost:{http_port}/health?status"

        try:
            response = await asyncio.to_thread(
                requests.get,
                url,
                timeout=5
            )

            probe_data = response.json()

            result = {
                "http_status": response.status_code,
                "overall_status": probe_data.get("overall_status"),
                "timestamp": probe_data.get("timestamp"),
                "probes": probe_data.get("probes", {}),
                "summary": probe_data.get("summary", {})
            }

            overall_status = probe_data.get("overall_status")
            logger.info(f"Probe status: {overall_status} (HTTP {response.status_code})")

            return format_response(
                ResponseEnvelope.success(
                    f"Probe status: {overall_status}",
                    data=result
                )
            )

        except requests.exceptions.ConnectionError:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    f"Cannot connect to HTTP server at {url}. Is the HTTP server running?",
                    data={"url": url}
                )
            )
        except requests.exceptions.Timeout:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    f"Timeout querying HTTP server at {url}",
                    data={"url": url}
                )
            )

    except Exception as e:
        logger.error(f"Failed to get probe status: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to get probe status: {str(e)}"
            )
        )


# Global auth manager (initialized by HTTP server if auth enabled)
_auth_manager = None


def set_auth_manager(auth_manager):
    """Set the global auth manager (called by HTTP server)."""
    global _auth_manager
    _auth_manager = auth_manager
    logger.info("Auth manager configured for MCP tools")


async def handle_create_auth_token(arguments: dict) -> list[types.TextContent]:
    """
    Handle create_auth_token tool.

    Requires auth manager to be configured (via HTTP server).
    """
    try:
        if not _auth_manager:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.INVALID_INPUT,
                    "Authentication not configured. Enable AUTH_ENABLED in HTTP server."
                )
            )

        logger.info("Creating auth token via MCP tool...")

        ttl_hours = arguments.get("ttl_hours", 24)
        metadata = arguments.get("metadata", {})

        # Create token
        result = await _auth_manager.create_token(
            client_id="mcp-tool",
            ttl_hours=ttl_hours,
            metadata=metadata
        )

        if not result:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.RATE_LIMITED,
                    "Rate limit exceeded for token creation"
                )
            )

        logger.info(f"Token created: {result['token_id']}")

        return format_response(
            ResponseEnvelope.success(
                f"Auth token created (expires in {result['ttl_hours']} hours)",
                data=result
            )
        )

    except Exception as e:
        logger.error(f"Failed to create auth token: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to create auth token: {str(e)}"
            )
        )


async def handle_revoke_auth_token(arguments: dict) -> list[types.TextContent]:
    """
    Handle revoke_auth_token tool.

    Requires auth manager to be configured (via HTTP server).
    """
    try:
        if not _auth_manager:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.INVALID_INPUT,
                    "Authentication not configured. Enable AUTH_ENABLED in HTTP server."
                )
            )

        token_id = arguments.get("token_id")

        if not token_id:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.INVALID_PARAMS,
                    "token_id is required"
                )
            )

        logger.info(f"Revoking auth token: {token_id}")

        # Revoke token
        success = await _auth_manager.revoke_token(token_id)

        if not success:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.NOT_FOUND,
                    f"Token not found: {token_id}"
                )
            )

        logger.info(f"Token revoked: {token_id}")

        return format_response(
            ResponseEnvelope.success(
                f"Auth token revoked: {token_id}",
                data={"token_id": token_id, "revoked": True}
            )
        )

    except Exception as e:
        logger.error(f"Failed to revoke auth token: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to revoke auth token: {str(e)}"
            )
        )


async def handle_list_active_tokens(arguments: dict) -> list[types.TextContent]:
    """
    Handle list_active_tokens tool.

    Requires auth manager to be configured (via HTTP server).
    """
    try:
        if not _auth_manager:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.INVALID_INPUT,
                    "Authentication not configured. Enable AUTH_ENABLED in HTTP server."
                )
            )

        logger.info("Listing active auth tokens...")

        # List active tokens
        tokens = await _auth_manager.list_active_tokens()

        logger.info(f"Found {len(tokens)} active tokens")

        result = {
            "total_active_tokens": len(tokens),
            "tokens": tokens
        }

        return format_response(
            ResponseEnvelope.success(
                f"Retrieved {len(tokens)} active tokens",
                data=result
            )
        )

    except Exception as e:
        logger.error(f"Failed to list active tokens: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to list active tokens: {str(e)}"
            )
        )


async def handle_analyze_health_trends(arguments: dict) -> list[types.TextContent]:
    """Handle analyze_health_trends tool."""
    try:
        time_window = arguments.get("time_window", "24h")
        server_filter = arguments.get("server_filter")

        logger.info(f"Analyzing health trends: window={time_window}, filter={server_filter}")

        result = await trends.analyze_health_trends(
            time_window=time_window,
            server_filter=server_filter
        )

        if result.get("ok"):
            return format_response(
                ResponseEnvelope.success(
                    result.get("message"),
                    data=result.get("data")
                )
            )
        else:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    result.get("message"),
                    data=result.get("data")
                )
            )

    except Exception as e:
        logger.error(f"Failed to analyze health trends: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to analyze health trends: {str(e)}"
            )
        )


async def handle_get_server_history(arguments: dict) -> list[types.TextContent]:
    """Handle get_server_history tool."""
    try:
        server_name = arguments.get("server_name")
        time_window = arguments.get("time_window", "24h")

        if not server_name:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.INVALID_PARAMS,
                    "server_name is required"
                )
            )

        logger.info(f"Getting server history: server={server_name}, window={time_window}")

        result = await trends.get_server_history(
            server_name=server_name,
            time_window=time_window
        )

        if result.get("ok"):
            return format_response(
                ResponseEnvelope.success(
                    result.get("message"),
                    data=result.get("data")
                )
            )
        else:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    result.get("message"),
                    data=result.get("data")
                )
            )

    except Exception as e:
        logger.error(f"Failed to get server history: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to get server history: {str(e)}"
            )
        )


async def handle_detect_degradations(arguments: dict) -> list[types.TextContent]:
    """Handle detect_degradations tool."""
    try:
        time_window = arguments.get("time_window", "24h")
        threshold = arguments.get("threshold", 20.0)

        logger.info(f"Detecting degradations: window={time_window}, threshold={threshold}%")

        result = await trends.detect_degradations(
            time_window=time_window,
            threshold=threshold
        )

        if result.get("ok"):
            return format_response(
                ResponseEnvelope.success(
                    result.get("message"),
                    data=result.get("data")
                )
            )
        else:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    result.get("message"),
                    data=result.get("data")
                )
            )

    except Exception as e:
        logger.error(f"Failed to detect degradations: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to detect degradations: {str(e)}"
            )
        )


async def handle_compare_time_periods(arguments: dict) -> list[types.TextContent]:
    """Handle compare_time_periods tool."""
    try:
        period1_start = arguments.get("period1_start")
        period1_end = arguments.get("period1_end")
        period2_start = arguments.get("period2_start")
        period2_end = arguments.get("period2_end")

        if not all([period1_start, period1_end, period2_start, period2_end]):
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.INVALID_PARAMS,
                    "All period timestamps are required"
                )
            )

        logger.info(f"Comparing time periods: p1={period1_start} to {period1_end}, p2={period2_start} to {period2_end}")

        result = await trends.compare_time_periods(
            period1_start=period1_start,
            period1_end=period1_end,
            period2_start=period2_start,
            period2_end=period2_end
        )

        if result.get("ok"):
            return format_response(
                ResponseEnvelope.success(
                    result.get("message"),
                    data=result.get("data")
                )
            )
        else:
            return format_response(
                ResponseEnvelope.error(
                    ErrorCodes.UNEXPECTED_EXCEPTION,
                    result.get("message"),
                    data=result.get("data")
                )
            )

    except Exception as e:
        logger.error(f"Failed to compare time periods: {e}")
        return format_response(
            ResponseEnvelope.error(
                ErrorCodes.UNEXPECTED_EXCEPTION,
                f"Failed to compare time periods: {str(e)}"
            )
        )


async def _run():
    """Run the MCP server (async)."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    """Entry point for the MCP server (sync wrapper for uvx)."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
