#!/usr/bin/env python3
"""
CLI Mode for diagnostic-mcp

Standalone command-line interface for running diagnostics without MCP client.
Useful for CI/CD pipelines, cron jobs, and automation.

Usage:
  python cli.py                                    # Run full diagnostic
  python cli.py --check health                     # Run health check only
  python cli.py --check health --quick             # Quick health check (1s timeout)
  python cli.py --check ports                      # Run port check only
  python cli.py --check config                     # Run config check only
  python cli.py --check tools                      # Run tools check only
  python cli.py --check readiness                  # Run readiness probe check
  python cli.py --check liveness                   # Run liveness probe check
  python cli.py --check probes                     # Run all probe checks
  python cli.py --format json                      # JSON output
  python cli.py --format text                      # Human-readable output (default)
  python cli.py --format summary                   # Summary only
  python cli.py --save-history                     # Save to Supabase
  python cli.py --timeout 3                        # Custom timeout for health checks (default: 2)
  python cli.py --call-tool check_all_health       # Call specific tool
  python cli.py --call-tool check_all_health --tool-args '{"timeout": 10}'  # With args
  python cli.py --export-config config.json        # Export config to file
  python cli.py --export-config config.md --export-format markdown  # Markdown export
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Add src directory to path
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Configure logging
logging.basicConfig(
    level=logging.WARNING,  # Only show warnings/errors in CLI mode
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger("diagnostic-mcp-cli")


class DiagnosticCLI:
    """CLI interface for diagnostic-mcp."""

    def __init__(self, args):
        self.args = args
        self.results = {}

    async def run(self):
        """Run the diagnostic checks based on CLI arguments."""
        from diagnostic_mcp.server import (
            check_port_consistency,
            check_all_health,
            check_configurations,
            check_tool_availability
        )
        import requests
        import os
        import time
        start_time = time.time()

        # Determine which checks to run
        check_type = self.args.check

        if check_type == "all" or check_type == "ports":
            logger.info("Running port consistency check...")
            self.results["port_check"] = await check_port_consistency()

        if check_type == "all" or check_type == "health":
            # Quick mode: use 1 second timeout and check critical servers only
            timeout = 1 if self.args.quick else self.args.timeout
            critical_only = self.args.quick
            mode = "critical servers" if critical_only else "all servers"
            if self.args.format == "text":
                print(f"Running health check ({mode}, timeout: {timeout}s)...", end="", flush=True)
            else:
                logger.info(f"Running health check ({mode}, timeout: {timeout}s)...")
            self.results["health_check"] = await check_all_health(timeout=timeout, critical_only=critical_only)
            if self.args.format == "text":
                print(" done")  # Newline after completion

        if check_type == "all" or check_type == "config":
            logger.info("Running configuration check...")
            self.results["config_check"] = await check_configurations()

        if check_type == "all" or check_type == "tools":
            logger.info("Running tool availability check...")
            self.results["tool_check"] = await check_tool_availability()

        if check_type == "readiness":
            logger.info("Running readiness probe check...")
            self.results["readiness_probe"] = await self.check_readiness_probe()

        if check_type == "liveness":
            logger.info("Running liveness probe check...")
            self.results["liveness_probe"] = await self.check_liveness_probe()

        if check_type == "probes":
            logger.info("Running all probe checks...")
            self.results["probe_status"] = await self.check_probe_status()

        # Calculate execution time
        execution_time_ms = int((time.time() - start_time) * 1000)

        # Save history if requested
        if self.args.save_history:
            await self.save_history(check_type, execution_time_ms)

        # Format and output results
        self.output_results()

        # Return appropriate exit code
        return self.get_exit_code()

    async def save_history(self, check_type: str, execution_time_ms: int):
        """Save diagnostic results to Supabase."""
        try:
            # Import after path setup
            from diagnostic_mcp.history import save_diagnostic_run, initialize_supabase
            from env_config import require_env

            # Initialize Supabase if not already done
            supabase_url = require_env("SUPABASE_URL")
            supabase_key = require_env("SUPABASE_KEY")
            initialize_supabase(supabase_url, supabase_key)

            # Save the diagnostic run
            record_id = await save_diagnostic_run(
                self.results,
                check_type=check_type,
                triggered_by="cli",
                execution_time_ms=execution_time_ms,
                timeout_seconds=self.args.timeout
            )

            if record_id:
                logger.info(f"Diagnostic history saved to Supabase: {record_id}")
            else:
                logger.warning("Failed to save diagnostic history (no record ID returned)")
        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    def output_results(self):
        """Output results in the requested format."""
        if self.args.format == "json":
            self.output_json()
        elif self.args.format == "summary":
            self.output_summary()
        else:  # text
            self.output_text()

    def output_json(self):
        """Output results as JSON."""
        output = {
            "timestamp": datetime.now().isoformat(),
            "checks": self.results,
            "summary": self.get_summary()
        }
        print(json.dumps(output, indent=2))

    def output_summary(self):
        """Output summary only."""
        summary = self.get_summary()
        print(f"Status: {summary['status'].upper()}")
        print(f"Total Issues: {summary['total_issues']}")
        print(f"Critical Issues: {summary['critical_issues']}")

        if summary['critical_issues'] > 0:
            print(f"\nOffline Servers: {summary.get('offline_servers', 0)}")

    def output_text(self):
        """Output human-readable text format."""
        print("=" * 80)
        print(f"Diagnostic Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        print()

        # Port check
        if "port_check" in self.results:
            self.print_port_check()

        # Health check
        if "health_check" in self.results:
            self.print_health_check()

        # Config check
        if "config_check" in self.results:
            self.print_config_check()

        # Tool check
        if "tool_check" in self.results:
            self.print_tool_check()

        # Probe checks
        if "readiness_probe" in self.results:
            self.print_probe_check("readiness", self.results["readiness_probe"])

        if "liveness_probe" in self.results:
            self.print_probe_check("liveness", self.results["liveness_probe"])

        if "probe_status" in self.results:
            self.print_probe_status(self.results["probe_status"])

        # Summary
        print()
        print("=" * 80)
        print("SUMMARY")
        print("=" * 80)
        summary = self.get_summary()
        print(f"Status: {summary['status'].upper()}")
        print(f"Total Issues: {summary['total_issues']}")
        print(f"Critical Issues: {summary['critical_issues']}")

    def print_port_check(self):
        """Print port consistency check results."""
        port_check = self.results["port_check"]
        print("PORT CONSISTENCY CHECK")
        print("-" * 80)

        if port_check.get("ok"):
            print("✓ No port conflicts detected")
        else:
            print("✗ Port conflicts found!")

        data = port_check.get("data", {})
        summary = data.get("summary", {})

        print(f"  Total Servers: {summary.get('total_servers', 0)}")
        print(f"  Stdio Servers: {summary.get('stdio_servers', 0)}")
        print(f"  SSE Servers: {summary.get('sse_servers', 0)}")
        print(f"  Conflicts: {summary.get('conflicts_count', 0)}")
        print()

    def print_health_check(self):
        """Print health check results."""
        health_check = self.results["health_check"]
        print("HEALTH CHECK")
        print("-" * 80)

        data = health_check.get("data", {})
        print(f"  Servers Online: {data.get('servers_online', 0)}/{data.get('total_checked', 0)}")
        print(f"  Servers Offline: {data.get('servers_offline', 0)}")
        print(f"  Servers Error: {data.get('servers_error', 0)}")

        # Show offline servers
        offline_servers = data.get("offline_servers", [])
        if offline_servers:
            print(f"\n  Offline Servers:")
            for server in offline_servers:
                print(f"    ✗ {server.get('name')}: {server.get('error')}")
                # Show enhanced diagnostics if available
                if "stderr" in server:
                    print(f"      stderr: {server['stderr'][:100]}...")
                if "running_processes" in server:
                    print(f"      Running processes: {len(server['running_processes'])}")
                if "alternative_transports" in server:
                    print(f"      Alternative transports: {len(server['alternative_transports'])}")

        print()

    def print_config_check(self):
        """Print configuration check results."""
        config_check = self.results["config_check"]
        print("CONFIGURATION CHECK")
        print("-" * 80)

        data = config_check.get("data", {})
        print(f"  Total Servers: {data.get('total_servers', 0)}")
        print(f"  Consistent Format: {data.get('consistent_format', 0)}")
        print(f"  Servers with Issues: {data.get('servers_with_issues', 0)}")

        issues = data.get("issues", [])
        if issues:
            print(f"\n  Configuration Issues:")
            for issue in issues:
                print(f"    ✗ {issue.get('server')}: {issue.get('issue')}")

        print()

    def print_tool_check(self):
        """Print tool availability check results."""
        tool_check = self.results["tool_check"]
        print("TOOL AVAILABILITY CHECK")
        print("-" * 80)

        data = tool_check.get("data", {})
        print(f"  Total Servers Configured: {data.get('total_servers_configured', 0)}")
        print(f"  Servers with Tools: {data.get('total_servers_with_tools', 0)}")
        print(f"  Total Tools Loaded: {data.get('total_tools_loaded', 0)}")

        conflicts = data.get("naming_conflicts", [])
        if conflicts:
            print(f"\n  Naming Conflicts ({len(conflicts)}):")
            for conflict in conflicts[:5]:  # Show first 5
                print(f"    ⚠ {conflict.get('tool_name')}: used by {', '.join(conflict.get('servers', []))}")

        print()

    def print_probe_check(self, probe_type: str, probe_result: Dict[str, Any]):
        """Print probe check results."""
        print(f"{probe_type.upper()} PROBE CHECK")
        print("-" * 80)

        if not probe_result.get("ok"):
            print(f"  ✗ Failed to query probe")
            print(f"  Error: {probe_result.get('error', 'Unknown error')}")
            print()
            return

        data = probe_result.get("data", {})
        status = data.get("status", "UNKNOWN")
        status_symbol = "✓" if status == "UP" else "✗"

        print(f"  {status_symbol} Status: {status}")
        print(f"  Timestamp: {data.get('timestamp', 'N/A')}")

        if probe_type == "readiness":
            print(f"  Degraded: {data.get('degraded', False)}")
            metrics = data.get("metrics", {})
            print(f"  Total Requests: {metrics.get('total_requests', 0)}")
            print(f"  Failed Requests: {metrics.get('failed_requests', 0)}")
            print(f"  Error Rate: {metrics.get('error_rate', 0)*100:.2f}%")
            print(f"  Uptime: {data.get('uptime_seconds', 0):.2f}s")

            if data.get("reason"):
                print(f"  Reason: {data.get('reason')}")
            if data.get("message"):
                print(f"  Message: {data.get('message')}")

        elif probe_type == "liveness":
            print(f"  Uptime: {data.get('uptime_seconds', 0):.2f}s")
            print(f"  Consecutive Failures: {data.get('consecutive_failures', 0)}")

            if data.get("reason"):
                print(f"  Reason: {data.get('reason')}")
            if data.get("message"):
                print(f"  Message: {data.get('message')}")

        print()

    def print_probe_status(self, probe_result: Dict[str, Any]):
        """Print comprehensive probe status."""
        print("COMPREHENSIVE PROBE STATUS")
        print("-" * 80)

        if not probe_result.get("ok"):
            print(f"  ✗ Failed to query probe status")
            print(f"  Error: {probe_result.get('error', 'Unknown error')}")
            print()
            return

        data = probe_result.get("data", {})
        overall_status = data.get("overall_status", "unknown")

        print(f"  Overall Status: {overall_status.upper()}")
        print(f"  Timestamp: {data.get('timestamp', 'N/A')}")
        print()

        probes = data.get("probes", {})

        # Startup probe
        startup = probes.get("startup", {})
        startup_symbol = "✓" if startup.get("status") == "UP" else "✗"
        print(f"  Startup Probe: {startup_symbol} {startup.get('status')}")
        print(f"    Complete: {startup.get('startup_complete', False)}")
        print(f"    Uptime: {startup.get('uptime_seconds', 0):.2f}s")

        # Liveness probe
        liveness = probes.get("liveness", {})
        liveness_symbol = "✓" if liveness.get("status") == "UP" else "✗"
        print(f"  Liveness Probe: {liveness_symbol} {liveness.get('status')}")
        print(f"    Consecutive Failures: {liveness.get('consecutive_failures', 0)}")

        # Readiness probe
        readiness = probes.get("readiness", {})
        readiness_symbol = "✓" if readiness.get("status") == "UP" else "✗"
        print(f"  Readiness Probe: {readiness_symbol} {readiness.get('status')}")
        print(f"    Degraded: {readiness.get('degraded', False)}")
        metrics = readiness.get("metrics", {})
        print(f"    Error Rate: {metrics.get('error_rate', 0)*100:.2f}%")

        print()

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all checks."""
        total_issues = 0
        critical_issues = 0
        offline_servers = 0

        # Count issues from each check
        for check_name, check_result in self.results.items():
            if not check_result.get("ok", True):
                total_issues += 1

        # Count critical issues (offline servers)
        if "health_check" in self.results:
            health_data = self.results["health_check"].get("data", {})
            offline_servers = health_data.get("servers_offline", 0)
            critical_issues += offline_servers

        # Determine overall status
        if critical_issues > 0:
            status = "critical"
        elif total_issues > 0:
            status = "degraded"
        else:
            status = "healthy"

        return {
            "status": status,
            "total_issues": total_issues,
            "critical_issues": critical_issues,
            "offline_servers": offline_servers
        }

    async def check_readiness_probe(self) -> Dict[str, Any]:
        """Check readiness probe via HTTP."""
        import requests
        import os

        http_port = int(os.environ.get("MCP_HTTP_PORT", "5555"))
        url = f"http://localhost:{http_port}/health?ready"

        try:
            response = requests.get(url, timeout=5)
            probe_data = response.json()

            return {
                "ok": response.status_code == 200,
                "data": probe_data
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": f"Failed to query readiness probe at {url}"
            }

    async def check_liveness_probe(self) -> Dict[str, Any]:
        """Check liveness probe via HTTP."""
        import requests
        import os

        http_port = int(os.environ.get("MCP_HTTP_PORT", "5555"))
        url = f"http://localhost:{http_port}/health?live"

        try:
            response = requests.get(url, timeout=5)
            probe_data = response.json()

            return {
                "ok": response.status_code == 200,
                "data": probe_data
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": f"Failed to query liveness probe at {url}"
            }

    async def check_probe_status(self) -> Dict[str, Any]:
        """Check comprehensive probe status via HTTP."""
        import requests
        import os

        http_port = int(os.environ.get("MCP_HTTP_PORT", "5555"))
        url = f"http://localhost:{http_port}/health?status"

        try:
            response = requests.get(url, timeout=5)
            probe_data = response.json()

            return {
                "ok": response.status_code == 200,
                "data": probe_data
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": f"Failed to query probe status at {url}"
            }

    def get_exit_code(self) -> int:
        """Get appropriate exit code based on results."""
        summary = self.get_summary()

        if summary["status"] == "critical":
            return 2  # Critical failure
        elif summary["status"] == "degraded":
            return 1  # Warning
        else:
            return 0  # Success


async def main_async():
    """Async main function."""
    parser = argparse.ArgumentParser(
        description="CLI interface for diagnostic-mcp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py                          # Run all checks
  python cli.py --check health           # Run health check only
  python cli.py --check health --quick   # Quick health check (1s timeout)
  python cli.py --format json            # JSON output
  python cli.py --save-history           # Save results to Supabase
  python cli.py --timeout 3              # Custom health check timeout
        """
    )

    parser.add_argument(
        "--check",
        choices=["all", "ports", "health", "config", "tools", "readiness", "liveness", "probes"],
        default="all",
        help="Which check(s) to run (default: all)"
    )

    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick health check mode: 1 second timeout, critical servers only"
    )

    parser.add_argument(
        "--format",
        choices=["text", "json", "summary"],
        default="text",
        help="Output format (default: text)"
    )

    parser.add_argument(
        "--save-history",
        action="store_true",
        help="Save diagnostic results to Supabase"
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=2,
        help="Timeout for health checks in seconds (default: 2, reduced for faster CLI execution)"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output (show debug logs)"
    )

    parser.add_argument(
        "--export-config",
        metavar="PATH",
        help="Export MCP server configurations to file (JSON/YAML/Markdown)"
    )

    parser.add_argument(
        "--export-format",
        choices=["json", "yaml", "markdown"],
        default="json",
        help="Export format for --export-config (default: json)"
    )

    parser.add_argument(
        "--export-include-health",
        action="store_true",
        help="Include health check results in config export"
    )

    parser.add_argument(
        "--export-include-tools",
        action="store_true",
        help="Include tool availability in config export"
    )

    parser.add_argument(
        "--call-tool",
        metavar="TOOL_NAME",
        help="Call a specific diagnostic tool (check_port_consistency, check_all_health, etc.)"
    )

    parser.add_argument(
        "--tool-args",
        metavar="JSON",
        help="JSON arguments for the tool (e.g., '{\"timeout\": 10}')"
    )

    args = parser.parse_args()

    # Adjust logging level if verbose
    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)
        logger.setLevel(logging.INFO)

    # Handle config export mode
    if args.export_config:
        from diagnostic_mcp.config_export import export_configurations, save_export

        logger.info(f"Exporting MCP configuration to {args.export_config} ({args.export_format} format)")

        export_data = await export_configurations(
            format=args.export_format,
            include_health=args.export_include_health,
            include_tools=args.export_include_tools
        )

        success = await save_export(export_data, args.export_config, format=args.export_format)

        if success:
            print(f"✓ Configuration exported to {args.export_config}")
            sys.exit(0)
        else:
            print(f"✗ Failed to export configuration to {args.export_config}")
            sys.exit(1)

    # Handle interactive tool calling mode
    if args.call_tool:
        from diagnostic_mcp.server import (
            check_port_consistency,
            check_all_health,
            check_configurations,
            check_tool_availability
        )
        from diagnostic_mcp.config_export import export_configurations

        # Parse tool arguments if provided
        tool_args = {}
        if args.tool_args:
            try:
                tool_args = json.loads(args.tool_args)
            except json.JSONDecodeError as e:
                print(f"✗ Invalid JSON in --tool-args: {e}")
                sys.exit(1)

        # Map tool names to functions
        from diagnostic_mcp.transport_testing import test_multi_transport

        available_tools = {
            "check_port_consistency": check_port_consistency,
            "check_all_health": check_all_health,
            "check_configurations": check_configurations,
            "check_tool_availability": check_tool_availability,
            "export_configuration": export_configurations,
            "test_multi_transport": test_multi_transport,
        }

        if args.call_tool not in available_tools:
            print(f"✗ Unknown tool: {args.call_tool}")
            print(f"Available tools: {', '.join(available_tools.keys())}")
            sys.exit(1)

        # Call the tool
        logger.info(f"Calling tool: {args.call_tool}")
        start_time = time.time()

        try:
            tool_func = available_tools[args.call_tool]
            result = await tool_func(**tool_args)

            execution_time_ms = int((time.time() - start_time) * 1000)

            # Output result
            if args.format == "json":
                output = {
                    "tool": args.call_tool,
                    "result": result,
                    "execution_time_ms": execution_time_ms,
                    "timestamp": datetime.now().isoformat()
                }
                print(json.dumps(output, indent=2))
            else:
                print(f"Tool: {args.call_tool}")
                print(f"Execution time: {execution_time_ms}ms")
                print(f"\nResult:")
                print(json.dumps(result, indent=2))

            # Return exit code based on result
            if isinstance(result, dict):
                if result.get("ok", True):
                    sys.exit(0)
                else:
                    sys.exit(1)
            else:
                sys.exit(0)

        except TypeError as e:
            print(f"✗ Invalid arguments for tool {args.call_tool}: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"✗ Tool execution failed: {e}")
            logger.error(f"Tool execution error: {e}", exc_info=True)
            sys.exit(1)

    # Run diagnostics
    cli = DiagnosticCLI(args)
    exit_code = await cli.run()

    return exit_code


def main():
    """Main entry point."""
    try:
        exit_code = asyncio.run(main_async())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
