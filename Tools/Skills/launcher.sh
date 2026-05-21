#!/usr/bin/env bash
set -euo pipefail

# AiNiee Skills Server launcher
# Starts the Skills HTTP server using the project's uv-managed Python.
#
# Usage:
#   ./Tools/Skills/launcher.sh [--port PORT] [--host HOST]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVER_PATH="$SCRIPT_DIR/server.py"

cd "$PROJECT_DIR"

echo "[Skills] Starting AiNiee Skills Server from $PROJECT_DIR" >&2
exec uv run python "$SERVER_PATH" "$@"
