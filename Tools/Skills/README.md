# AiNiee Skills — Lightweight AI Tool Interaction Framework

A lightweight, **MCP-free** alternative for AI-driven AiNiee interaction. Skills provides a simple REST/JSON interface for core operations without any dependency on the MCP protocol, FastAPI, or uvicorn.

## Why Skills Instead of MCP?

The MCP server (`Tools/MCPServer/`) is a full implementation of the [Model Context Protocol](https://modelcontextprotocol.io), which requires:
- The `mcp` Python package (FastMCP)
- `fastapi` + `uvicorn` for HTTP transport
- JSON-RPC 2.0 message format
- Route auto-discovery from the WebServer

Skills takes a different approach:
- **Zero extra dependencies** — pure Python standard library (`http.server`, `json`)
- **Simple REST/JSON** — not JSON-RPC, just HTTP methods and JSON bodies
- **Curated operations** — predefined skills covering core workflows, not auto-discovered routes
- **Multiple execution modes** — skills can run via HTTP server, CLI, or direct Python calls
- **Composable** — skills can be chained or called programmatically from scripts

## Quick Start

### Start the Skills Server

From the main menu:
```
Select "Start Skills Server" (option 18)
```

From the command line:
```bash
uv run ainiee_cli.py skills server --port 8766
```

### Check the Server is Running

```bash
curl http://127.0.0.1:8766/health
```

Response:
```json
{"status": "ok", "service": "ainiee-skills", "skills_count": 6}
```

### List Available Skills

```bash
curl http://127.0.0.1:8766/skills
```

## Available Skills

| Skill | Category | Description |
|-------|----------|-------------|
| `system` | system | System information and health checks |
| `config` | config | Read and write profile configuration |
| `translate` | task | Execute translation tasks |
| `queue` | queue | Manage the task queue |
| `profile` | config | Manage configuration profiles |
| `file` | files | File discovery and staging |

## API Reference

### `GET /health`
Health check endpoint.

### `GET /skills`
List all available skills with descriptions, parameters, and examples.

### `GET /skills/{name}`
Get details for a specific skill.

**Example:**
```bash
curl http://127.0.0.1:8766/skills/system
```

### `POST /skills/{name}`
Execute a skill with arguments.

**Ping:**
```bash
curl -X POST http://127.0.0.1:8766/skills/system \
  -H "Content-Type: application/json" \
  -d '{"action": "ping"}'
```
Response:
```json
{"success": true, "data": {"pong": true}}
```

**Get config:**
```bash
curl -X POST http://127.0.0.1:8766/skills/config \
  -H "Content-Type: application/json" \
  -d '{"action": "get", "key": "target_platform"}'
```

**List profiles:**
```bash
curl -X POST http://127.0.0.1:8766/skills/profile \
  -H "Content-Type: application/json" \
  -d '{"action": "list"}'
```

**Start a translation:**
```bash
curl -X POST http://127.0.0.1:8766/skills/translate \
  -H "Content-Type: application/json" \
  -d '{
    "action": "run",
    "task_type": "translate",
    "input_path": "/path/to/file.txt",
    "source_lang": "Japanese",
    "target_lang": "Chinese",
    "profile": "default"
  }'
```

## CLI Mode

Skills can be used directly from the command line without starting the HTTP server:

```bash
# List all skills
uv run ainiee_cli.py skills list

# Get skill details
uv run ainiee_cli.py skills describe config

# Execute a skill
uv run ainiee_cli.py skills run system '{"action": "ping"}'

# Start the HTTP server
uv run ainiee_cli.py skills server --port 8766
```

## Architecture

```
Tools/Skills/
├── README.md              # This file
├── __init__.py            # Package exports
├── skill_base.py          # Skill, SkillRegistry, SkillResult base classes
├── server.py              # HTTP server (stdlib http.server)
├── cli.py                 # CLI runner
├── runtime.py             # Runtime readiness checks
├── launcher.sh            # Shell launcher script
└── skills/
    ├── __init__.py        # Registry builder (registers all skills)
    ├── system_skill.py    # System info and health
    ├── config_skill.py    # Configuration management
    ├── translate_skill.py # Translation task execution
    ├── queue_skill.py     # Task queue management
    ├── profile_skill.py   # Profile management
    └── file_skill.py      # File operations
```

## Execution Modes (混合模式)

Skills support three execution modes, selected automatically:

1. **Direct (preferred)**: Skills call AiNiee internal APIs directly when imported in-process
2. **CLI subprocess**: Skills spawn `uv run ainiee_cli.py` subprocesses for task execution
3. **WebServer proxy**: Skills proxy through the WebServer HTTP API when available

## Comparison: MCP vs Skills

| Feature | MCP Server | Skills Server |
|---------|-----------|---------------|
| Protocol | JSON-RPC 2.0 | REST/JSON |
| Dependencies | mcp, fastapi, uvicorn | stdlib only |
| Transport | stdio / streamable-http / SSE | HTTP |
| Route discovery | Auto (all /api/*) | Manual (curated) |
| Execution modes | WebServer proxy | Direct / CLI / WebServer |
| CLI integration | `ainiee mcp` | `ainiee skills` |
| Menu option | Option 17 | Option 18 |
| Default port | 8765 | 8766 |

## Extending

To add a new skill:

1. Create a new file in `Tools/Skills/skills/` (e.g., `my_skill.py`)
2. Subclass `Skill` and implement `meta` and `execute`
3. Register it in `Tools/Skills/skills/__init__.py`

Example:

```python
from Tools.Skills.skill_base import Skill, SkillMeta, SkillParameter, SkillResult

class MySkill(Skill):
    @property
    def meta(self):
        return SkillMeta(
            name="my_skill",
            description="Does something useful.",
            category="custom",
            parameters=[SkillParameter(name="input", type="string", required=True)],
        )

    def execute(self, args):
        return SkillResult.ok({"processed": args.get("input", "")})
```
