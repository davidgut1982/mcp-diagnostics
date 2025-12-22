#!/usr/bin/env python3
"""
HTTP/SSE Transport Wrapper for diagnostic-mcp

Exposes the diagnostic-mcp MCP server over HTTP/SSE for use with
the ContextForge gateway and other SSE-compatible clients.

Endpoints:
  GET  /sse        - SSE connection for MCP protocol
  POST /messages/  - Message endpoint for MCP protocol
  GET  /health     - Health check endpoint
  GET  /info       - Server info endpoint

Usage:
  python sse_server.py                    # Default port 5583
  python sse_server.py --port 6583        # Custom port
  MCP_SSE_PORT=5583 python sse_server.py  # Via environment
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

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

# Add shared utilities path
shared_path = Path(__file__).parent.parent.parent / "shared"
if str(shared_path) not in sys.path:
    sys.path.insert(0, str(shared_path))

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("diagnostic-mcp-sse")


class _SseResponse(Response):
    """
    No-op Response for SSE endpoints.

    The SSE transport handles the response directly via ASGI send callback.
    This response class satisfies Starlette's expectation of a return value
    without actually sending anything (since SSE already handled it).
    """
    async def __call__(self, scope, receive, send):
        # Do nothing - SSE transport already sent the response
        pass


def initialize_mcp_server():
    """
    Initialize the diagnostic-mcp server.

    Returns the configured MCP server instance ready for use.
    """
    # Import the server module
    from diagnostic_mcp import server as mcp_server_module

    logger.info("Initialized diagnostic-mcp MCP server")

    return mcp_server_module.app


def create_app(mcp_server):
    """Create Starlette app with MCP SSE endpoints and CORS support."""

    # Initialize SSE transport with message endpoint
    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        """Handle SSE connection for MCP protocol."""
        logger.info(f"SSE connection from {request.client.host if request.client else 'unknown'}")

        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp_server.run(
                streams[0], streams[1], mcp_server.create_initialization_options()
            )

        # Return no-op response - SSE transport already sent everything
        return _SseResponse()

    async def health(request):
        """Health check endpoint."""
        return JSONResponse({
            "status": "healthy",
            "server": "diagnostic-mcp",
            "transport": "sse"
        })

    async def info(request):
        """Server info endpoint."""
        return JSONResponse({
            "name": "diagnostic-mcp",
            "version": "0.1.0",
            "transport": "sse",
            "protocol": "mcp",
            "endpoints": {
                "sse": "/sse",
                "messages": "/messages/",
                "health": "/health",
                "info": "/info",
                "trends": "/trends",
                "trends_server": "/trends/{server_name}",
                "trends_degradations": "/trends/degradations",
                "trends_compare": "/trends/compare"
            }
        })

    async def trends_overview(request):
        """Overall trend analysis endpoint."""
        from diagnostic_mcp import trends

        # Get query parameters
        window = request.query_params.get("window", "24h")

        # Call trend analysis function
        result = await trends.analyze_health_trends(time_window=window)

        if result.get("ok"):
            return JSONResponse(result.get("data"))
        else:
            return JSONResponse(
                {"error": result.get("error"), "message": result.get("message")},
                status_code=500
            )

    async def trends_server(request):
        """Server-specific trend analysis endpoint."""
        from diagnostic_mcp import trends

        # Get path parameter
        server_name = request.path_params.get("server_name")
        window = request.query_params.get("window", "24h")

        # Call server history function
        result = await trends.get_server_history(
            server_name=server_name,
            time_window=window
        )

        if result.get("ok"):
            return JSONResponse(result.get("data"))
        else:
            return JSONResponse(
                {"error": result.get("error"), "message": result.get("message")},
                status_code=404 if result.get("error") == "no_data" else 500
            )

    async def trends_degradations(request):
        """Degradation detection endpoint."""
        from diagnostic_mcp import trends

        # Get query parameters
        window = request.query_params.get("window", "24h")
        threshold = float(request.query_params.get("threshold", "20.0"))

        # Call degradation detection function
        result = await trends.detect_degradations(
            time_window=window,
            threshold=threshold
        )

        if result.get("ok"):
            return JSONResponse(result.get("data"))
        else:
            return JSONResponse(
                {"error": result.get("error"), "message": result.get("message")},
                status_code=500
            )

    async def trends_compare(request):
        """Period comparison endpoint."""
        from diagnostic_mcp import trends

        # Get query parameters
        p1_start = request.query_params.get("p1_start")
        p1_end = request.query_params.get("p1_end")
        p2_start = request.query_params.get("p2_start")
        p2_end = request.query_params.get("p2_end")

        if not all([p1_start, p1_end, p2_start, p2_end]):
            return JSONResponse(
                {"error": "missing_parameters", "message": "All period timestamps are required"},
                status_code=400
            )

        # Call comparison function
        result = await trends.compare_time_periods(
            period1_start=p1_start,
            period1_end=p1_end,
            period2_start=p2_start,
            period2_end=p2_end
        )

        if result.get("ok"):
            return JSONResponse(result.get("data"))
        else:
            return JSONResponse(
                {"error": result.get("error"), "message": result.get("message")},
                status_code=500
            )

    # Define routes
    routes = [
        Route("/health", endpoint=health, methods=["GET"]),
        Route("/info", endpoint=info, methods=["GET"]),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
        # Trend analysis endpoints
        Route("/trends", endpoint=trends_overview, methods=["GET"]),
        Route("/trends/{server_name}", endpoint=trends_server, methods=["GET"]),
        Route("/trends/degradations", endpoint=trends_degradations, methods=["GET"]),
        Route("/trends/compare", endpoint=trends_compare, methods=["GET"]),
    ]

    # Configure CORS middleware for Docker network and gateway access
    # ContextForge gateway connects from Docker network at 172.17.0.1
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:*",
                "http://127.0.0.1:*",
                "http://172.17.0.1:*",  # Docker bridge network
                "http://172.17.0.*:*",  # Docker containers
                "*"  # Allow all origins for development
            ],
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )
    ]

    return Starlette(routes=routes, middleware=middleware)


def main():
    """Run the diagnostic-mcp SSE server."""
    parser = argparse.ArgumentParser(
        description="HTTP/SSE wrapper for diagnostic-mcp"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=int(os.environ.get("MCP_SSE_PORT", "5583")),
        help="HTTP port to listen on (default: 5583)"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MCP_SSE_HOST", "0.0.0.0"),
        help="Host to bind to (default: 0.0.0.0)"
    )

    args = parser.parse_args()

    # Initialize the MCP server
    logger.info("Initializing diagnostic-mcp server...")
    mcp_server = initialize_mcp_server()

    # Create and run the HTTP app
    app = create_app(mcp_server)

    logger.info(f"Starting diagnostic-mcp SSE server on {args.host}:{args.port}")
    logger.info(f"Health check: http://{args.host}:{args.port}/health")
    logger.info(f"SSE endpoint: http://{args.host}:{args.port}/sse")
    logger.info(f"Messages endpoint: http://{args.host}:{args.port}/messages/")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
