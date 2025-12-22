"""
Configuration Export Module

Exports MCP server configurations for backup, migration, and documentation.
Supports multiple export formats and filtering options.

Usage:
    from diagnostic_mcp.config_export import export_configurations, export_to_json

    # Export all configurations to JSON
    config_data = export_configurations(format='json')

    # Export specific servers
    config_data = export_configurations(servers=['knowledge-mcp', 'docker-mcp'])

    # Export with enhanced diagnostics
    config_data = export_configurations(include_health=True)
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml

logger = logging.getLogger(__name__)


def load_mcp_servers_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load MCP servers configuration from mcp_servers.json.

    Args:
        config_path: Path to mcp_servers.json (default: ~/.claude/mcp_servers.json)

    Returns:
        Dictionary containing MCP server configurations
    """
    if config_path is None:
        # Default Claude Code config location
        config_path = str(Path.home() / ".claude" / "mcp_servers.json")

    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        logger.info(f"Loaded MCP configuration from {config_path}")
        return config
    except FileNotFoundError:
        logger.error(f"MCP configuration not found at {config_path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse MCP configuration: {e}")
        return {}


async def export_configurations(
    format: str = "json",
    servers: Optional[List[str]] = None,
    include_health: bool = False,
    include_tools: bool = False,
    config_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Export MCP server configurations in the specified format.

    Args:
        format: Export format ('json', 'yaml', 'markdown')
        servers: List of server names to export (None for all)
        include_health: Include health check results
        include_tools: Include tool availability information
        config_path: Path to mcp_servers.json

    Returns:
        Dictionary containing exported configuration data
    """
    # Load base configuration
    config = load_mcp_servers_config(config_path)

    if not config:
        return {
            "error": "Failed to load MCP configuration",
            "timestamp": datetime.now().isoformat()
        }

    # Filter servers if specified
    if servers:
        mcpServers = config.get("mcpServers", {})
        filtered_servers = {k: v for k, v in mcpServers.items() if k in servers}
        config["mcpServers"] = filtered_servers

    # Build export data
    export_data = {
        "timestamp": datetime.now().isoformat(),
        "source": "diagnostic-mcp",
        "version": "2.0.0",
        "total_servers": len(config.get("mcpServers", {})),
        "configuration": config
    }

    # Add health check data if requested
    if include_health:
        from diagnostic_mcp.server import check_all_health
        health_result = await check_all_health(timeout=5)
        export_data["health_status"] = health_result

    # Add tool availability data if requested
    if include_tools:
        from diagnostic_mcp.server import check_tool_availability
        tools_result = await check_tool_availability()
        export_data["tool_availability"] = tools_result

    return export_data


def export_to_json(export_data: Dict[str, Any], indent: int = 2) -> str:
    """
    Convert export data to JSON string.

    Args:
        export_data: Export data dictionary
        indent: JSON indentation level

    Returns:
        JSON string
    """
    return json.dumps(export_data, indent=indent, ensure_ascii=False)


def export_to_yaml(export_data: Dict[str, Any]) -> str:
    """
    Convert export data to YAML string.

    Args:
        export_data: Export data dictionary

    Returns:
        YAML string
    """
    return yaml.dump(export_data, default_flow_style=False, sort_keys=False)


def export_to_markdown(export_data: Dict[str, Any]) -> str:
    """
    Convert export data to Markdown documentation.

    Args:
        export_data: Export data dictionary

    Returns:
        Markdown string
    """
    md_lines = []

    # Header
    md_lines.append("# MCP Server Configuration Export")
    md_lines.append(f"\n**Generated**: {export_data.get('timestamp', 'unknown')}")
    md_lines.append(f"**Source**: {export_data.get('source', 'unknown')}")
    md_lines.append(f"**Total Servers**: {export_data.get('total_servers', 0)}")
    md_lines.append("")

    # Table of contents
    md_lines.append("## Table of Contents")
    md_lines.append("")
    servers = export_data.get("configuration", {}).get("mcpServers", {})
    for i, server_name in enumerate(servers.keys(), 1):
        md_lines.append(f"{i}. [{server_name}](#{server_name.lower().replace('-', '')})")
    md_lines.append("")

    # Server configurations
    md_lines.append("## Server Configurations")
    md_lines.append("")

    for server_name, server_config in servers.items():
        md_lines.append(f"### {server_name}")
        md_lines.append("")

        # Command
        command = server_config.get("command")
        if command:
            md_lines.append(f"**Command**: `{command}`")
            md_lines.append("")

        # Arguments
        args = server_config.get("args", [])
        if args:
            md_lines.append("**Arguments**:")
            md_lines.append("```")
            for arg in args:
                md_lines.append(f"  {arg}")
            md_lines.append("```")
            md_lines.append("")

        # Environment variables
        env = server_config.get("env", {})
        if env:
            md_lines.append("**Environment Variables**:")
            md_lines.append("")
            md_lines.append("| Variable | Value |")
            md_lines.append("|----------|-------|")
            for key, value in env.items():
                # Mask sensitive values
                if any(sensitive in key.lower() for sensitive in ['key', 'secret', 'token', 'password']):
                    value = "***REDACTED***"
                md_lines.append(f"| `{key}` | `{value}` |")
            md_lines.append("")

        md_lines.append("---")
        md_lines.append("")

    # Health status if included
    if "health_status" in export_data:
        md_lines.append("## Health Status")
        md_lines.append("")
        health = export_data["health_status"]
        health_data = health.get("data", {})

        md_lines.append(f"- **Servers Online**: {health_data.get('servers_online', 0)}/{health_data.get('total_checked', 0)}")
        md_lines.append(f"- **Servers Offline**: {health_data.get('servers_offline', 0)}")
        md_lines.append(f"- **Servers with Errors**: {health_data.get('servers_error', 0)}")
        md_lines.append("")

        offline_servers = health_data.get("offline_servers", [])
        if offline_servers:
            md_lines.append("### Offline Servers")
            md_lines.append("")
            for server in offline_servers:
                md_lines.append(f"- **{server.get('name')}**: {server.get('error', 'unknown error')}")
            md_lines.append("")

    # Tool availability if included
    if "tool_availability" in export_data:
        md_lines.append("## Tool Availability")
        md_lines.append("")
        tools = export_data["tool_availability"]
        tools_data = tools.get("data", {})

        md_lines.append(f"- **Total Servers with Tools**: {tools_data.get('total_servers_with_tools', 0)}")
        md_lines.append(f"- **Total Tools Loaded**: {tools_data.get('total_tools_loaded', 0)}")
        md_lines.append("")

        conflicts = tools_data.get("naming_conflicts", [])
        if conflicts:
            md_lines.append("### Tool Name Conflicts")
            md_lines.append("")
            md_lines.append("| Tool Name | Servers |")
            md_lines.append("|-----------|---------|")
            for conflict in conflicts:
                servers_str = ", ".join(conflict.get('servers', []))
                md_lines.append(f"| `{conflict.get('tool_name')}` | {servers_str} |")
            md_lines.append("")

    return "\n".join(md_lines)


async def save_export(
    export_data: Dict[str, Any],
    output_path: str,
    format: str = "json"
) -> bool:
    """
    Save export data to a file.

    Args:
        export_data: Export data dictionary
        output_path: Path to save the file
        format: Export format ('json', 'yaml', 'markdown')

    Returns:
        True if save succeeded, False otherwise
    """
    try:
        if format == "json":
            content = export_to_json(export_data)
        elif format == "yaml":
            content = export_to_yaml(export_data)
        elif format == "markdown":
            content = export_to_markdown(export_data)
        else:
            logger.error(f"Unsupported export format: {format}")
            return False

        with open(output_path, 'w') as f:
            f.write(content)

        logger.info(f"Exported configuration to {output_path} ({format} format)")
        return True

    except Exception as e:
        logger.error(f"Failed to save export to {output_path}: {e}")
        return False
