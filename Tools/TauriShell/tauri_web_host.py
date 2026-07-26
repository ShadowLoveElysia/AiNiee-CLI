#!/usr/bin/env python3
"""Host process for Tauri PoC.

Starts AiNiee FastAPI web server and keeps it alive until terminated.
"""

import argparse
import signal
import sys
import threading
import time
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start AiNiee WebServer for Tauri shell.")
    parser.add_argument("--host", default="127.0.0.1", help="WebServer host address.")
    parser.add_argument("--port", type=int, default=18000, help="WebServer port.")
    parser.add_argument(
        "--allow-remote-access",
        action="store_true",
        help="Allow non-loopback WebServer binding for this process only.",
    )
    parser.add_argument(
        "--monitor-mode",
        action="store_true",
        help="Start backend in monitor mode.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.allow_remote_access and args.host == "127.0.0.1":
        args.host = "0.0.0.0"

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    dist_index = project_root / "Tools" / "WebServer" / "dist" / "index.html"
    if not dist_index.exists():
        print(
            "WebServer dist assets were not found. Build or install Tools/WebServer/dist first.",
            file=sys.stderr,
        )
        return 2

    try:
        from Tools.WebServer.web_server import run_server, stop_server
        from ModuleFolders.UserInterface.AppI18N import initialize_i18n
    except Exception as exc:
        print(f"Failed to import WebServer runtime: {exc}", file=sys.stderr)
        return 3

    stop_event = threading.Event()

    _, i18n = initialize_i18n(str(project_root))
    if not args.allow_remote_access:
        from rich.console import Console

        Console().print(f"[yellow]{i18n.get('warning_remote_access_disabled')}[/yellow]")

    def _request_stop(_sig, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    server_thread = run_server(
        host=args.host,
        port=args.port,
        monitor_mode=args.monitor_mode,
        allow_remote_access=args.allow_remote_access,
    )
    if server_thread is None:
        print("WebServer failed to start.", file=sys.stderr)
        return 4

    print(f"AiNiee WebServer started at http://{args.host}:{args.port}")

    try:
        while not stop_event.is_set() and server_thread.is_alive():
            time.sleep(0.2)
    finally:
        try:
            stop_server()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
