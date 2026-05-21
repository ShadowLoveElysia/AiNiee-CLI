"""
Skills CLI Runner

Allows executing skills directly from the command line without starting the HTTP server.
This is the foundation for the "混合模式" — skills can be invoked via CLI, HTTP, or direct API.

Usage:
    uv run ainiee_cli.py skills list
    uv run ainiee_cli.py skills describe system
    uv run ainiee_cli.py skills run system '{"action": "ping"}'
    uv run ainiee_cli.py skills run config '{"action": "get", "key": "model"}'
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List


def run_cli(args: List[str]) -> int:
    """Entry point for the 'skills' subcommand."""
    parser = argparse.ArgumentParser(
        prog="ainiee skills",
        description="Execute or manage AiNiee skills.",
    )
    sub = parser.add_subparsers(dest="command")

    # skills list
    list_parser = sub.add_parser("list", help="List all available skills.")

    # skills describe <name>
    describe_parser = sub.add_parser("describe", help="Describe a skill and its parameters.")
    describe_parser.add_argument("name", help="Skill name (e.g., system, config, translate)")

    # skills run <name> [args]
    run_parser = sub.add_parser("run", help="Execute a skill.")
    run_parser.add_argument("name", help="Skill name to execute.")
    run_parser.add_argument("args_json", nargs="?", default="{}", help="JSON string of arguments.")

    # skills server
    server_parser = sub.add_parser("server", help="Start the Skills HTTP server.")
    server_parser.add_argument("--host", default="127.0.0.1", help="Host address.")
    server_parser.add_argument("--port", type=int, default=8766, help="Port number.")

    parsed = parser.parse_args(args)

    if parsed.command == "list":
        return _cmd_list()
    elif parsed.command == "describe":
        return _cmd_describe(parsed.name)
    elif parsed.command == "run":
        return _cmd_run(parsed.name, parsed.args_json)
    elif parsed.command == "server":
        return _cmd_server(parsed.host, parsed.port)
    else:
        parser.print_help()
        return 1


def _get_registry():
    """Lazy-import the registry to avoid circular imports at module level."""
    from Tools.Skills.skills import build_registry  # noqa: PLC0415
    return build_registry()


def _cmd_list() -> int:
    registry = _get_registry()
    skills = registry.list_skills()
    print(f"Available skills ({len(skills)}):")
    for s in skills:
        print(f"  {s['name']:25s}  {s['description']}")
    return 0


def _cmd_describe(name: str) -> int:
    registry = _get_registry()
    try:
        meta = registry.get_skill_meta(name)
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_run(name: str, args_json: str) -> int:
    registry = _get_registry()
    try:
        skill_args: Dict[str, Any] = json.loads(args_json)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON arguments: {e}", file=sys.stderr)
        return 1

    try:
        result = registry.execute(name, skill_args)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.success else 1
    except Exception as e:
        print(f"Execution error: {e}", file=sys.stderr)
        return 1


def _cmd_server(host: str, port: int) -> int:
    from Tools.Skills.server import run_server  # noqa: PLC0415
    print(f"Starting Skills HTTP server on http://{host}:{port}")
    try:
        run_server(host=host, port=port)
    except KeyboardInterrupt:
        pass
    return 0
