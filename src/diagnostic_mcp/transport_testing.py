"""
Multi-Transport Testing Module

Tests MCP servers across multiple transport types (stdio, HTTP/SSE) to detect:
- Dual-transport configurations (server accessible via both stdio and HTTP)
- Transport compatibility issues
- Configuration inconsistencies
- Performance differences between transports

Usage:
    from diagnostic_mcp.transport_testing import test_multi_transport

    result = await test_multi_transport(timeout=10)
"""

import asyncio
import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import httpx

logger = logging.getLogger(__name__)


def load_mcp_servers_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load MCP servers configuration from mcp_servers.json."""
    if config_path is None:
        config_path = str(Path.home() / ".claude" / "mcp_servers.json")

    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        logger.error(f"MCP configuration not found at {config_path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse MCP configuration: {e}")
        return {}


async def test_stdio_transport(server_name: str, server_config: Dict[str, Any], timeout: int = 5) -> Dict[str, Any]:
    """
    Test server via stdio transport.

    Args:
        server_name: Name of the server
        server_config: Server configuration from mcp_servers.json
        timeout: Timeout in seconds

    Returns:
        Test result with status and details
    """
    result = {
        "transport": "stdio",
        "status": "unknown",
        "accessible": False,
        "error": None,
        "response_time_ms": None
    }

    command = server_config.get("command")
    args = server_config.get("args", [])
    env = server_config.get("env", {})

    if not command:
        result["status"] = "error"
        result["error"] = "No command specified in configuration"
        return result

    try:
        start_time = asyncio.get_event_loop().time()

        # Build full command
        full_command = [command] + args

        # Start the process
        process = await asyncio.create_subprocess_exec(
            *full_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**server_config.get("env", {})}
        )

        # Send MCP initialize request
        initialize_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "diagnostic-mcp-transport-test",
                    "version": "1.0.0"
                }
            }
        }

        request_json = json.dumps(initialize_request) + "\n"

        # Write request
        process.stdin.write(request_json.encode())
        await process.stdin.drain()

        # Read response with timeout
        try:
            response_line = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=timeout
            )

            if response_line:
                response_time_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)

                # Try to parse response
                try:
                    response = json.loads(response_line.decode())
                    if "result" in response:
                        result["status"] = "online"
                        result["accessible"] = True
                        result["response_time_ms"] = response_time_ms
                        result["server_info"] = response.get("result", {}).get("serverInfo", {})
                    else:
                        result["status"] = "error"
                        result["error"] = "Invalid initialize response"
                except json.JSONDecodeError as e:
                    result["status"] = "error"
                    result["error"] = f"Failed to parse response: {e}"
            else:
                result["status"] = "offline"
                result["error"] = "No response received"

        except asyncio.TimeoutError:
            result["status"] = "timeout"
            result["error"] = f"No response within {timeout} seconds"

        # Cleanup
        try:
            process.kill()
            await process.wait()
        except:
            pass

    except FileNotFoundError:
        result["status"] = "error"
        result["error"] = f"Command not found: {command}"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


async def test_http_transport(server_name: str, port: int, timeout: int = 5) -> Dict[str, Any]:
    """
    Test server via HTTP/SSE transport.

    Args:
        server_name: Name of the server
        port: HTTP port to test
        timeout: Timeout in seconds

    Returns:
        Test result with status and details
    """
    result = {
        "transport": "http",
        "port": port,
        "status": "unknown",
        "accessible": False,
        "error": None,
        "response_time_ms": None
    }

    url = f"http://localhost:{port}/health"

    try:
        start_time = asyncio.get_event_loop().time()

        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=timeout)

            response_time_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)

            if response.status_code == 200:
                result["status"] = "online"
                result["accessible"] = True
                result["response_time_ms"] = response_time_ms
                result["health_data"] = response.json()
            else:
                result["status"] = "error"
                result["error"] = f"HTTP {response.status_code}"

    except httpx.ConnectError:
        result["status"] = "offline"
        result["error"] = "Connection refused"
    except httpx.TimeoutException:
        result["status"] = "timeout"
        result["error"] = f"No response within {timeout} seconds"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


async def detect_http_port(server_name: str, server_config: Dict[str, Any]) -> Optional[int]:
    """
    Detect HTTP port for a server from configuration or common patterns.

    Args:
        server_name: Name of the server
        server_config: Server configuration

    Returns:
        Port number if detected, None otherwise
    """
    # Check for explicit port in env
    env = server_config.get("env", {})

    # Common port environment variables
    port_vars = ["MCP_HTTP_PORT", "HTTP_PORT", "PORT", "SERVER_PORT"]
    for var in port_vars:
        if var in env:
            try:
                return int(env[var])
            except ValueError:
                pass

    # Check for port in args
    args = server_config.get("args", [])
    for i, arg in enumerate(args):
        if arg in ["--port", "-p"] and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except ValueError:
                pass

    # Common default ports for known servers
    default_ports = {
        "diagnostic-mcp": 5555,
        "knowledge-mcp": 5556,
        "docker-mcp": 5557,
    }

    return default_ports.get(server_name)


async def test_multi_transport(
    timeout: int = 5,
    servers: Optional[List[str]] = None,
    config_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Test MCP servers across multiple transport types.

    Args:
        timeout: Timeout for each transport test in seconds
        servers: List of server names to test (None for all)
        config_path: Path to mcp_servers.json

    Returns:
        Dictionary containing multi-transport test results
    """
    logger.info(f"Starting multi-transport testing (timeout: {timeout}s)")

    # Load configuration
    config = load_mcp_servers_config(config_path)
    if not config:
        return {
            "ok": False,
            "error": "Failed to load MCP configuration",
            "timestamp": datetime.now().isoformat()
        }

    mcp_servers = config.get("mcpServers", {})

    # Filter servers if specified
    if servers:
        mcp_servers = {k: v for k, v in mcp_servers.items() if k in servers}

    # Test results
    results = {
        "servers_tested": 0,
        "dual_transport_servers": [],
        "stdio_only_servers": [],
        "http_only_servers": [],
        "offline_servers": [],
        "transport_details": {}
    }

    # Test each server
    for server_name, server_config in mcp_servers.items():
        logger.info(f"Testing {server_name} on multiple transports...")

        server_results = {
            "server": server_name,
            "transports": {}
        }

        # Test stdio transport
        stdio_result = await test_stdio_transport(server_name, server_config, timeout)
        server_results["transports"]["stdio"] = stdio_result

        # Detect and test HTTP transport
        http_port = await detect_http_port(server_name, server_config)
        if http_port:
            http_result = await test_http_transport(server_name, http_port, timeout)
            server_results["transports"]["http"] = http_result

        # Categorize server
        stdio_accessible = stdio_result.get("accessible", False)
        http_accessible = server_results["transports"].get("http", {}).get("accessible", False)

        if stdio_accessible and http_accessible:
            results["dual_transport_servers"].append(server_name)
            server_results["category"] = "dual_transport"
        elif stdio_accessible and not http_accessible:
            results["stdio_only_servers"].append(server_name)
            server_results["category"] = "stdio_only"
        elif not stdio_accessible and http_accessible:
            results["http_only_servers"].append(server_name)
            server_results["category"] = "http_only"
        else:
            results["offline_servers"].append(server_name)
            server_results["category"] = "offline"

        results["transport_details"][server_name] = server_results
        results["servers_tested"] += 1

    # Calculate summary
    results["summary"] = {
        "total_servers": results["servers_tested"],
        "dual_transport_count": len(results["dual_transport_servers"]),
        "stdio_only_count": len(results["stdio_only_servers"]),
        "http_only_count": len(results["http_only_servers"]),
        "offline_count": len(results["offline_servers"])
    }

    # Determine overall status
    if results["servers_tested"] == 0:
        results["ok"] = False
        results["error"] = "No servers tested"
    else:
        results["ok"] = True

    results["timestamp"] = datetime.now().isoformat()

    logger.info(f"Multi-transport testing complete: {results['summary']}")

    return results
