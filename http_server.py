#!/usr/bin/env python3
"""
HTTP/SSE Transport Wrapper for diagnostic-mcp

Exposes the diagnostic-mcp MCP server over HTTP/SSE with health endpoints
for integration with Kubernetes, load balancers, and monitoring systems.

Endpoints:
  GET  /sse                - SSE connection for MCP protocol
  POST /messages/          - Message endpoint for MCP protocol
  GET  /health             - Basic health check
  GET  /health?live        - Liveness probe
  GET  /health?ready       - Readiness probe
  GET  /health?startup     - Startup probe
  GET  /health?status      - Comprehensive probe status
  GET  /health/startup     - Startup probe (direct route)
  GET  /health/status      - Probe status (direct route)
  GET  /info               - Server info endpoint
  GET  /diagnostics        - Run full diagnostic (non-MCP endpoint)
  POST /tool/{tool_name}   - Call specific diagnostic tool (non-MCP endpoint)

Health Probe Configuration:
  --allowed-rejections N     - Max rejections before unready (default: 100)
  --sampling-interval N      - Sampling window in seconds (default: 10)
  --recovery-interval N      - Recovery time in seconds (default: 2x sampling)
  --startup-duration N       - Startup phase duration in seconds (default: 30)
  --degraded-threshold F     - Error rate for degraded state (default: 0.25)

Usage:
  python http_server.py                          # Default port 5555
  python http_server.py --port 5555              # Custom port
  python http_server.py --startup-duration 60    # 60s startup window
  MCP_HTTP_PORT=5555 python http_server.py       # Via environment
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, Response
from mcp.server.sse import SseServerTransport

# Add src directory to path for imports
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("diagnostic-mcp-http")


class _SseResponse(Response):
    """
    No-op Response for SSE endpoints.

    The SSE transport handles the response directly via ASGI send callback.
    """
    async def __call__(self, scope, receive, send):
        # Do nothing - SSE transport already sent the response
        pass


class HealthMonitor:
    """
    Tracks server health for liveness, readiness, and startup probes.

    Implements rejection-based readiness tracking similar to Apollo MCP Server,
    with enhanced probe logic for Kubernetes-style health monitoring.

    Probe States:
    - Startup: Server is initializing (startup_duration_seconds)
    - Ready: Server is accepting requests (rejection-based)
    - Live: Server is alive and not deadlocked (always UP unless critical)
    - Degraded: Server is live but experiencing issues

    Configurable Thresholds:
    - allowed_rejections: Max rejections before marking unready
    - sampling_interval_seconds: Window for counting rejections
    - recovery_interval_seconds: Time to wait before marking ready again
    - startup_duration_seconds: How long to report "starting" state
    - degraded_threshold: Rejection percentage for degraded state
    """

    def __init__(
        self,
        allowed_rejections: int = 100,
        sampling_interval_seconds: int = 10,
        recovery_interval_seconds: Optional[int] = None,
        startup_duration_seconds: int = 30,
        degraded_threshold: float = 0.25
    ):
        self.allowed_rejections = allowed_rejections
        self.sampling_interval = timedelta(seconds=sampling_interval_seconds)
        self.recovery_interval = timedelta(
            seconds=recovery_interval_seconds or (sampling_interval_seconds * 2)
        )
        self.startup_duration = timedelta(seconds=startup_duration_seconds)
        self.degraded_threshold = degraded_threshold

        # State tracking
        self.server_start_time = datetime.now()
        self.rejection_count = 0
        self.last_sampling_reset = datetime.now()
        self.is_ready = False  # Start unready until startup completes
        self.is_live = True
        self.unready_since: Optional[datetime] = None
        self.total_requests = 0
        self.failed_requests = 0
        self.last_health_check = datetime.now()
        self.failure_count = 0

    def record_request(self, success: bool):
        """Record a request outcome."""
        self.total_requests += 1
        self.last_health_check = datetime.now()

        if not success:
            self.failed_requests += 1
            self.rejection_count += 1
            self.failure_count += 1
        else:
            # Reset failure count on success
            self.failure_count = 0

        # Check if we should reset sampling interval
        now = datetime.now()
        if now - self.last_sampling_reset > self.sampling_interval:
            self._check_readiness(now)
            self.rejection_count = 0
            self.last_sampling_reset = now

    def _check_readiness(self, now: datetime):
        """Check if server should be marked ready/unready."""
        # Check if still in startup phase
        if now - self.server_start_time < self.startup_duration:
            # Still starting up - remain unready
            return

        if self.rejection_count > self.allowed_rejections and self.is_ready:
            # Mark unready
            self.is_ready = False
            self.unready_since = now
            logger.warning(
                f"Server marked UNREADY: {self.rejection_count} rejections "
                f"in {self.sampling_interval.total_seconds()}s "
                f"(threshold: {self.allowed_rejections})"
            )
        elif not self.is_ready and self.unready_since:
            # Check if recovery period has elapsed
            if now - self.unready_since > self.recovery_interval:
                self.is_ready = True
                self.unready_since = None
                logger.info("Server marked READY: recovery period elapsed")
        elif not self.is_ready and not self.unready_since:
            # Startup completed successfully
            self.is_ready = True
            logger.info("Server marked READY: startup completed")

    def get_startup_status(self) -> Dict[str, Any]:
        """
        Get startup probe status.

        Returns UP once startup duration has elapsed, DOWN otherwise.
        """
        now = datetime.now()
        uptime = (now - self.server_start_time).total_seconds()
        startup_complete = uptime >= self.startup_duration.total_seconds()

        result = {
            "status": "UP" if startup_complete else "DOWN",
            "timestamp": now.isoformat(),
            "uptime_seconds": round(uptime, 2),
            "startup_duration_seconds": self.startup_duration.total_seconds(),
            "startup_complete": startup_complete
        }

        if not startup_complete:
            result["startup_remaining_seconds"] = round(
                self.startup_duration.total_seconds() - uptime, 2
            )

        return result

    def get_liveness(self) -> Dict[str, Any]:
        """
        Get liveness probe status.

        Always UP unless critical failure detected (e.g., repeated failures).
        Checks for deadlock-like conditions.
        """
        now = datetime.now()
        uptime = (now - self.server_start_time).total_seconds()

        # Check for critical failure: too many consecutive failures
        is_live = self.failure_count < 10  # 10 consecutive failures = critical

        result = {
            "status": "UP" if is_live else "DOWN",
            "timestamp": now.isoformat(),
            "uptime_seconds": round(uptime, 2),
            "last_health_check": self.last_health_check.isoformat(),
            "consecutive_failures": self.failure_count
        }

        if not is_live:
            result["reason"] = "critical_failure_threshold_exceeded"
            result["message"] = f"Server has {self.failure_count} consecutive failures"

        return result

    def get_readiness(self) -> Dict[str, Any]:
        """
        Get readiness probe status with enhanced metadata.

        Returns UP if ready to accept traffic, DOWN otherwise.
        Includes degraded state when experiencing issues but not fully unready.
        """
        # Force check in case sampling interval hasn't elapsed
        now = datetime.now()
        if now - self.last_sampling_reset > self.sampling_interval:
            self._check_readiness(now)

        # Calculate current error rate for degraded state
        error_rate = 0.0
        if self.total_requests > 0:
            error_rate = self.failed_requests / self.total_requests

        # Determine state
        is_degraded = error_rate >= self.degraded_threshold and self.is_ready

        status = "UP" if self.is_ready else "DOWN"

        result = {
            "status": status,
            "timestamp": now.isoformat(),
            "degraded": is_degraded,
            "metrics": {
                "total_requests": self.total_requests,
                "failed_requests": self.failed_requests,
                "current_rejections": self.rejection_count,
                "rejection_threshold": self.allowed_rejections,
                "error_rate": round(error_rate, 4),
                "degraded_threshold": self.degraded_threshold
            },
            "uptime_seconds": round((now - self.server_start_time).total_seconds(), 2)
        }

        if not self.is_ready and self.unready_since:
            result["unready_since"] = self.unready_since.isoformat()
            result["recovery_in_seconds"] = max(0, round(
                (self.recovery_interval - (now - self.unready_since)).total_seconds(), 2
            ))
            result["reason"] = "rejection_threshold_exceeded"
        elif not self.is_ready:
            result["reason"] = "startup_incomplete"
            result["startup_remaining_seconds"] = max(0, round(
                (self.startup_duration - (now - self.server_start_time)).total_seconds(), 2
            ))

        if is_degraded:
            result["message"] = f"Server degraded: {error_rate*100:.1f}% error rate"

        return result

    def get_probe_status(self) -> Dict[str, Any]:
        """
        Get comprehensive probe status (all probes).

        Returns combined status of startup, liveness, and readiness probes.
        """
        startup = self.get_startup_status()
        liveness = self.get_liveness()
        readiness = self.get_readiness()

        # Determine overall health
        # Priority: critical > starting > unready > degraded > healthy
        overall_status = "healthy"
        if liveness["status"] == "DOWN":
            overall_status = "critical"
        elif startup["status"] == "DOWN":
            overall_status = "starting"
        elif readiness["status"] == "DOWN":
            overall_status = "unready"
        elif readiness.get("degraded", False):
            overall_status = "degraded"

        return {
            "overall_status": overall_status,
            "timestamp": datetime.now().isoformat(),
            "probes": {
                "startup": startup,
                "liveness": liveness,
                "readiness": readiness
            },
            "summary": {
                "startup_complete": startup["startup_complete"],
                "is_live": liveness["status"] == "UP",
                "is_ready": readiness["status"] == "UP",
                "is_degraded": readiness.get("degraded", False),
                "uptime_seconds": readiness["uptime_seconds"]
            }
        }


def initialize_mcp_server():
    """
    Initialize the diagnostic-mcp server.

    Returns the configured MCP server instance ready for use.
    """
    # Import the server module
    from diagnostic_mcp import server as diagnostic_server

    logger.info("Initialized diagnostic-mcp server")

    return diagnostic_server.app


def create_app(mcp_server, health_monitor: HealthMonitor, auth_manager=None):
    """Create Starlette app with MCP SSE endpoints, health endpoints, authentication, and CORS support."""

    # Initialize SSE transport with message endpoint
    sse = SseServerTransport("/messages/")

    # Authentication middleware
    async def auth_middleware(request, call_next):
        """
        Authentication middleware.

        Checks Authorization header for Bearer tokens.
        Exempt endpoints: /health*, /info, /auth/token (bootstrap)
        """
        # Skip auth if not enabled
        if not auth_manager:
            return await call_next(request)

        # Exempt endpoints
        exempt_paths = ["/health", "/info", "/auth/token"]
        if any(request.url.path.startswith(path) for path in exempt_paths):
            return await call_next(request)

        # Check Authorization header
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return JSONResponse({
                "status": "error",
                "error": "Missing Authorization header",
                "message": "Bearer token required",
                "timestamp": datetime.now().isoformat()
            }, status_code=401)

        # Parse Bearer token
        if not auth_header.startswith("Bearer "):
            return JSONResponse({
                "status": "error",
                "error": "Invalid Authorization header format",
                "message": "Must be 'Bearer <token>'",
                "timestamp": datetime.now().isoformat()
            }, status_code=401)

        token = auth_header[7:]  # Remove "Bearer " prefix

        # Validate token
        is_valid = await auth_manager.validate_token(token)

        if not is_valid:
            logger.warning(f"Invalid token from {request.client.host if request.client else 'unknown'}")
            return JSONResponse({
                "status": "error",
                "error": "Invalid or expired token",
                "message": "Authentication failed",
                "timestamp": datetime.now().isoformat()
            }, status_code=401)

        # Token valid - proceed
        return await call_next(request)

    async def handle_sse(request):
        """Handle SSE connection for MCP protocol."""
        logger.info(f"SSE connection from {request.client.host if request.client else 'unknown'}")

        try:
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                health_monitor.record_request(True)
                await mcp_server.run(
                    streams[0], streams[1], mcp_server.create_initialization_options()
                )
        except Exception as e:
            logger.error(f"SSE connection error: {e}")
            health_monitor.record_request(False)
            raise

        # Return no-op response - SSE transport already sent everything
        return _SseResponse()

    async def health_basic(request):
        """Basic health check - always returns UP."""
        return JSONResponse({
            "status": "UP",
            "server": "diagnostic-mcp",
            "transport": "http",
            "timestamp": datetime.now().isoformat()
        })

    async def health_live(request):
        """Liveness probe - checks if server is alive."""
        result = health_monitor.get_liveness()
        status_code = 200 if result["status"] == "UP" else 503
        return JSONResponse(result, status_code=status_code)

    async def health_ready(request):
        """Readiness probe - checks if server is ready to handle requests."""
        result = health_monitor.get_readiness()
        status_code = 200 if result["status"] == "UP" else 503
        return JSONResponse(result, status_code=status_code)

    async def health_startup(request):
        """Startup probe - checks if server has completed startup."""
        result = health_monitor.get_startup_status()
        status_code = 200 if result["status"] == "UP" else 503
        return JSONResponse(result, status_code=status_code)

    async def health_status(request):
        """Comprehensive probe status - all probes combined."""
        result = health_monitor.get_probe_status()
        # Return 200 unless critically unhealthy
        status_code = 503 if result["overall_status"] == "critical" else 200
        return JSONResponse(result, status_code=status_code)

    async def health(request):
        """
        Health check endpoint with query parameter support.

        GET /health         - Basic health check
        GET /health?live    - Liveness probe
        GET /health?ready   - Readiness probe
        GET /health?startup - Startup probe
        GET /health?status  - Comprehensive probe status
        """
        if "live" in request.query_params:
            return await health_live(request)
        elif "ready" in request.query_params:
            return await health_ready(request)
        elif "startup" in request.query_params:
            return await health_startup(request)
        elif "status" in request.query_params:
            return await health_status(request)
        else:
            return await health_basic(request)

    async def info(request):
        """Server info endpoint."""
        return JSONResponse({
            "name": "diagnostic-mcp",
            "version": "2.1.0",
            "transport": "http",
            "protocol": "mcp",
            "endpoints": {
                "sse": "/sse",
                "messages": "/messages/",
                "health": "/health",
                "health_live": "/health?live",
                "health_ready": "/health?ready",
                "health_startup": "/health?startup",
                "health_status": "/health?status",
                "health_startup_direct": "/health/startup",
                "health_status_direct": "/health/status",
                "info": "/info",
                "diagnostics": "/diagnostics",
                "call_tool": "/tool/{tool_name}"
            },
            "tools": [
                "check_all_health",
                "check_configurations",
                "check_port_consistency",
                "check_tool_availability",
                "run_full_diagnostic",
                "export_configuration",
                "test_multi_transport",
                "check_readiness_probe",
                "check_liveness_probe",
                "get_probe_status"
            ],
            "features": {
                "health_monitoring": True,
                "readiness_probes": True,
                "liveness_probes": True,
                "startup_probes": True,
                "degraded_state_detection": True,
                "http_diagnostics": True,
                "enhanced_diagnostics_v2": True,
                "interactive_tool_calling": True,
                "configuration_export": True
            },
            "health_config": {
                "allowed_rejections": health_monitor.allowed_rejections,
                "sampling_interval_seconds": int(health_monitor.sampling_interval.total_seconds()),
                "recovery_interval_seconds": int(health_monitor.recovery_interval.total_seconds()),
                "startup_duration_seconds": int(health_monitor.startup_duration.total_seconds()),
                "degraded_threshold": health_monitor.degraded_threshold
            }
        })

    async def call_tool_endpoint(request):
        """
        Call a specific diagnostic tool via HTTP (non-MCP).

        POST /tool/{tool_name}
        Body: JSON arguments for the tool

        Example:
          POST /tool/check_all_health
          {"timeout": 10}
        """
        start_time = time.time()

        try:
            # Get tool name from path
            tool_name = request.path_params.get("tool_name")

            # Parse request body for tool arguments
            try:
                tool_args = await request.json()
            except:
                tool_args = {}

            # Import diagnostic functions
            from diagnostic_mcp.server import (
                check_port_consistency,
                check_all_health,
                check_configurations,
                check_tool_availability
            )
            from diagnostic_mcp.config_export import export_configurations
            from diagnostic_mcp.transport_testing import test_multi_transport

            # Map tool names to functions
            available_tools = {
                "check_port_consistency": check_port_consistency,
                "check_all_health": check_all_health,
                "check_configurations": check_configurations,
                "check_tool_availability": check_tool_availability,
                "export_configuration": export_configurations,
                "test_multi_transport": test_multi_transport,
            }

            if tool_name not in available_tools:
                return JSONResponse({
                    "status": "error",
                    "error": f"Unknown tool: {tool_name}",
                    "available_tools": list(available_tools.keys()),
                    "timestamp": datetime.now().isoformat()
                }, status_code=404)

            # Call the tool
            logger.info(f"HTTP tool call: {tool_name} with args {tool_args}")
            tool_func = available_tools[tool_name]
            result = await tool_func(**tool_args)

            # Calculate execution time
            execution_time_ms = int((time.time() - start_time) * 1000)

            # Build response
            response = {
                "tool": tool_name,
                "result": result,
                "execution_time_ms": execution_time_ms,
                "timestamp": datetime.now().isoformat()
            }

            health_monitor.record_request(True)

            return JSONResponse(response, status_code=200)

        except TypeError as e:
            logger.error(f"Invalid tool arguments: {e}")
            health_monitor.record_request(False)
            return JSONResponse({
                "status": "error",
                "error": f"Invalid arguments for tool {tool_name}: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }, status_code=400)
        except Exception as e:
            logger.error(f"Tool call error: {e}", exc_info=True)
            health_monitor.record_request(False)
            return JSONResponse({
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }, status_code=500)

    async def diagnostics(request):
        """
        Run full diagnostic via HTTP endpoint (non-MCP).

        This allows external monitoring systems to run diagnostics
        without using the MCP protocol.
        """
        start_time = time.time()

        try:
            # Import diagnostic functions
            from diagnostic_mcp.server import (
                check_port_consistency,
                check_all_health,
                check_configurations,
                check_tool_availability
            )
            from diagnostic_mcp.history import save_diagnostic_run

            # Run all checks
            port_check = await check_port_consistency()
            health_check = await check_all_health(timeout=5)
            config_check = await check_configurations()
            tool_check = await check_tool_availability()

            # Calculate execution time
            execution_time_ms = int((time.time() - start_time) * 1000)

            # Determine overall status
            total_issues = (
                (0 if port_check.get("ok") else 1) +
                (0 if health_check.get("ok") else 1) +
                (0 if config_check.get("ok") else 1) +
                (0 if tool_check.get("ok") else 1)
            )

            critical_issues = 0
            if health_check.get("data"):
                critical_issues = health_check["data"].get("servers_offline", 0)

            status = "healthy" if total_issues == 0 else "degraded" if critical_issues == 0 else "critical"

            # Save to history
            results = {
                "port_check": port_check,
                "health_check": health_check,
                "config_check": config_check,
                "tool_check": tool_check
            }

            record_id = await save_diagnostic_run(
                results,
                check_type="all",
                triggered_by="http",
                execution_time_ms=execution_time_ms,
                timeout_seconds=5
            )

            result = {
                "timestamp": datetime.now().isoformat(),
                "status": status,
                "summary": {
                    "total_issues": total_issues,
                    "critical_issues": critical_issues
                },
                "checks": {
                    "port_consistency": port_check,
                    "health": health_check,
                    "configuration": config_check,
                    "tools": tool_check
                },
                "execution_time_ms": execution_time_ms
            }

            if record_id:
                result["history_id"] = record_id

            health_monitor.record_request(total_issues == 0)

            # Return 200 if healthy/degraded, 503 if critical
            status_code = 200 if status != "critical" else 503

            return JSONResponse(result, status_code=status_code)

        except Exception as e:
            logger.error(f"Diagnostic error: {e}", exc_info=True)
            health_monitor.record_request(False)
            return JSONResponse({
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }, status_code=500)

    async def create_token_endpoint(request):
        """
        Create authentication token endpoint (POST /auth/token).

        Requires admin token in Authorization header for bootstrapping.
        Returns new session token with expiration.
        """
        if not auth_manager:
            return JSONResponse({
                "status": "error",
                "error": "Authentication not enabled",
                "message": "Set AUTH_ENABLED=true to enable authentication",
                "timestamp": datetime.now().isoformat()
            }, status_code=503)

        # Check admin token
        auth_header = request.headers.get("Authorization")

        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse({
                "status": "error",
                "error": "Admin token required",
                "message": "Authorization: Bearer <admin_token>",
                "timestamp": datetime.now().isoformat()
            }, status_code=401)

        admin_token = auth_header[7:]

        # Validate admin token
        is_valid_admin = await auth_manager.validate_token(admin_token)

        if not is_valid_admin:
            logger.warning(f"Invalid admin token from {request.client.host if request.client else 'unknown'}")
            return JSONResponse({
                "status": "error",
                "error": "Invalid admin token",
                "timestamp": datetime.now().isoformat()
            }, status_code=401)

        # Parse request body for TTL and metadata
        try:
            body = await request.json()
            ttl_hours = body.get("ttl_hours", 24)
            metadata = body.get("metadata", {})
        except:
            ttl_hours = 24
            metadata = {}

        # Get client ID for rate limiting
        client_id = request.client.host if request.client else "unknown"

        # Create token
        result = await auth_manager.create_token(
            client_id=client_id,
            ttl_hours=ttl_hours,
            metadata=metadata
        )

        if not result:
            return JSONResponse({
                "status": "error",
                "error": "Rate limit exceeded",
                "message": "Too many token creation requests",
                "timestamp": datetime.now().isoformat()
            }, status_code=429)

        logger.info(f"Token created via HTTP: {result['token_id']} (client: {client_id})")

        # Return token
        return JSONResponse({
            "status": "success",
            "message": "Token created successfully",
            "data": result,
            "timestamp": datetime.now().isoformat()
        }, status_code=201)

    # Define routes
    routes = [
        Route("/health", endpoint=health, methods=["GET"]),
        Route("/health/startup", endpoint=health_startup, methods=["GET"]),
        Route("/health/status", endpoint=health_status, methods=["GET"]),
        Route("/info", endpoint=info, methods=["GET"]),
        Route("/diagnostics", endpoint=diagnostics, methods=["GET"]),
        Route("/tool/{tool_name}", endpoint=call_tool_endpoint, methods=["POST"]),
        Route("/auth/token", endpoint=create_token_endpoint, methods=["POST"]),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
    ]

    # Configure CORS middleware
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Allow all origins for development
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )
    ]

    # Add auth middleware if authentication is enabled
    if auth_manager:
        from starlette.middleware.base import BaseHTTPMiddleware

        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                return await auth_middleware(request, call_next)

        middleware.append(Middleware(AuthMiddleware))
        logger.info("Authentication middleware enabled")

    return Starlette(routes=routes, middleware=middleware)


def main():
    """Run the diagnostic-mcp HTTP server."""
    parser = argparse.ArgumentParser(
        description="HTTP/SSE wrapper for diagnostic-mcp with health endpoints"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=int(os.environ.get("MCP_HTTP_PORT", "5555")),
        help="HTTP port to listen on (default: 5555)"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_HTTP_HOST", "0.0.0.0"),
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--allowed-rejections",
        type=int,
        default=100,
        help="Maximum rejections before marking unready (default: 100)"
    )
    parser.add_argument(
        "--sampling-interval",
        type=int,
        default=10,
        help="Sampling interval in seconds (default: 10)"
    )
    parser.add_argument(
        "--recovery-interval",
        type=int,
        help="Recovery interval in seconds (default: 2x sampling interval)"
    )
    parser.add_argument(
        "--startup-duration",
        type=int,
        default=30,
        help="Startup duration in seconds (default: 30)"
    )
    parser.add_argument(
        "--degraded-threshold",
        type=float,
        default=0.25,
        help="Error rate threshold for degraded state (default: 0.25)"
    )

    args = parser.parse_args()

    # Initialize health monitor
    health_monitor = HealthMonitor(
        allowed_rejections=args.allowed_rejections,
        sampling_interval_seconds=args.sampling_interval,
        recovery_interval_seconds=args.recovery_interval,
        startup_duration_seconds=args.startup_duration,
        degraded_threshold=args.degraded_threshold
    )

    # Initialize Supabase for history tracking and auth
    supabase_client = None
    try:
        # Add shared utilities path for env_config
        shared_path = Path(__file__).parent.parent.parent / "shared"
        if str(shared_path) not in sys.path:
            sys.path.insert(0, str(shared_path))

        from env_config import require_env
        from diagnostic_mcp.history import initialize_supabase

        supabase_url = require_env("SUPABASE_URL")
        supabase_key = require_env("SUPABASE_KEY")
        initialize_supabase(supabase_url, supabase_key)

        # Keep reference for auth storage
        from supabase import create_client
        supabase_client = create_client(supabase_url, supabase_key)

        logger.info("Supabase initialized for diagnostic history tracking")
    except Exception as e:
        logger.warning(f"Failed to initialize Supabase (history tracking disabled): {e}")

    # Initialize authentication if enabled
    auth_manager = None
    auth_enabled = os.environ.get("AUTH_ENABLED", "false").lower() == "true"

    if auth_enabled:
        try:
            from diagnostic_mcp.auth import (
                AuthManager,
                MemoryTokenStorage,
                SupabaseTokenStorage,
                RateLimiter
            )

            # Get auth configuration
            admin_token = os.environ.get("AUTH_ADMIN_TOKEN")
            if not admin_token:
                logger.error("AUTH_ENABLED=true but AUTH_ADMIN_TOKEN not set")
                raise ValueError("AUTH_ADMIN_TOKEN required when AUTH_ENABLED=true")

            ttl_hours = int(os.environ.get("AUTH_TOKEN_TTL", "24"))
            storage_type = os.environ.get("AUTH_STORAGE", "memory").lower()

            # Initialize storage backend
            if storage_type == "supabase":
                if not supabase_client:
                    raise ValueError("Supabase client required for AUTH_STORAGE=supabase")
                storage = SupabaseTokenStorage(supabase_client)
                logger.info("Using Supabase token storage")
            else:
                storage = MemoryTokenStorage()
                logger.info("Using in-memory token storage (not persistent)")

            # Initialize auth manager
            auth_manager = AuthManager(
                storage=storage,
                admin_token=admin_token,
                default_ttl_hours=ttl_hours,
                rate_limiter=RateLimiter(max_attempts=5, window_seconds=60)
            )

            logger.info(f"Authentication enabled (TTL: {ttl_hours}h, Storage: {storage_type})")

        except Exception as e:
            logger.error(f"Failed to initialize authentication: {e}")
            raise
    else:
        logger.info("Authentication disabled (set AUTH_ENABLED=true to enable)")

    # Initialize the MCP server
    logger.info("Initializing diagnostic-mcp server...")
    mcp_server = initialize_mcp_server()

    # Set auth manager for MCP tools (if auth enabled)
    if auth_manager:
        from diagnostic_mcp import server as diagnostic_server
        diagnostic_server.set_auth_manager(auth_manager)

    # Create and run the HTTP app
    app = create_app(mcp_server, health_monitor, auth_manager)

    logger.info(f"Starting diagnostic-mcp HTTP server on {args.host}:{args.port}")
    logger.info(f"Health check: http://{args.host}:{args.port}/health")
    logger.info(f"Liveness probe: http://{args.host}:{args.port}/health?live")
    logger.info(f"Readiness probe: http://{args.host}:{args.port}/health?ready")
    logger.info(f"Startup probe: http://{args.host}:{args.port}/health?startup")
    logger.info(f"Probe status: http://{args.host}:{args.port}/health?status")
    logger.info(f"Diagnostics: http://{args.host}:{args.port}/diagnostics")
    logger.info(f"SSE endpoint: http://{args.host}:{args.port}/sse")
    logger.info(f"Info: http://{args.host}:{args.port}/info")

    if auth_enabled:
        logger.info(f"Auth endpoint: POST http://{args.host}:{args.port}/auth/token")
        logger.info("Authentication: Bearer <token> required for protected endpoints")

    logger.info(f"Health config: rejections={args.allowed_rejections}, "
                f"sampling={args.sampling_interval}s, "
                f"startup={args.startup_duration}s, "
                f"degraded={args.degraded_threshold}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
