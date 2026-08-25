# Tools/WebServer/web_server.py
import os
import sys
import json
import re
import secrets
import shutil
import threading
import subprocess
import time
import collections
import locale
from datetime import datetime
from typing import List, Dict, Any, Optional

# --- Pre-emptive Import for FastAPI & Pydantic ---
try:
    import uvicorn
    from fastapi import FastAPI, HTTPException, Body, File, UploadFile, Response, BackgroundTasks, Query, Request, APIRouter
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel, ConfigDict, model_validator
except ImportError:
    # This error will be caught and handled in ainiee_cli.py
    raise ImportError("Required packages are missing. Please run 'uv add fastapi uvicorn[standard] pydantic python-multipart'.,Or run 'uv sync'")

# --- Add Project Root to Python Path ---
# This ensures that we can import modules from the main project (e.g., ainiee_cli)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
UPDATETEMP_PATH = os.path.join(PROJECT_ROOT, "updatetemp") # Define upload directory
TEMP_EDIT_PATH = os.path.join(PROJECT_ROOT, "output", "temp_edit") # Define draft directory

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ModuleFolders.Infrastructure.MangaFeatureGuard import get_manga_feature_status
from ModuleFolders.Infrastructure.TaskContract import (
    TASK_API_KEY_ENV,
    TaskContractError,
    TaskSpec,
    build_cli_args,
    normalize_task_name,
)
from ModuleFolders.Infrastructure.LLMRequester.SdkRequestMode import sync_sdk_request_mode_config
from ModuleFolders.Infrastructure.Cache.CacheItem import TranslationStatus
from ModuleFolders.Infrastructure.RemoteAccessPolicy import (
    REMOTE_ACCESS_CONFIG_KEY,
    ensure_bind_allowed,
    is_loopback_bind_host,
)
from Tools.MCPServer.security import (
    AUTH_REQUIRED_HEADER,
    INTERNAL_AUTH_ENV,
    INTERNAL_AUTH_HEADER,
    MCP_AUTH_HEADER,
    MCP_SECRET_PLACEHOLDER,
    WEB_SESSION_AUTH_REQUIRED,
    WEB_SESSION_COOKIE_NAME,
    contains_redacted_secret,
    is_mcp_request,
    restore_redacted_json_text,
    restore_redacted_secrets,
    sanitize_data_for_mcp,
    sanitize_json_text_for_mcp,
    strip_mcp_security_metadata,
)
from ModuleFolders.Infrastructure.TaskConfig.ConfigProfileService import (
    RULE_PROFILE_KEYS,
    atomic_write_json,
    deep_merge,
    list_profile_names,
    load_effective_config,
    load_json_file,
    load_master_preset,
    load_root_config,
    normalize_rules_payload,
    resolve_profile_path,
    save_effective_config,
    save_root_config,
    save_rule_value,
    save_setting_value,
    sanitize_profile_name,
    split_effective_config,
)

_MANGA_OPTIONAL_HINT = "主程序其它功能不受影响；只有在使用漫画翻译时才需要补齐漫画模块。"

try:
    from ModuleFolders.MangaCore.api import router as manga_router
    _MANGA_ROUTER_IMPORT_ERROR = None
except Exception as exc:
    _MANGA_ROUTER_IMPORT_ERROR = exc
    manga_router = APIRouter(prefix="/api/manga", tags=["manga"])

    @manga_router.api_route("", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def manga_api_unavailable_root():
        raise HTTPException(
            status_code=503,
            detail=f"漫画模块当前不可用。{_MANGA_OPTIONAL_HINT} 导入错误: {_MANGA_ROUTER_IMPORT_ERROR}",
        )

    @manga_router.api_route("/{asset_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def manga_api_unavailable(asset_path: str):
        _ = asset_path
        raise HTTPException(
            status_code=503,
            detail=f"漫画模块当前不可用。{_MANGA_OPTIONAL_HINT} 导入错误: {_MANGA_ROUTER_IMPORT_ERROR}",
        )

# --- Global State & Task Management ---

WEB_TASK_API_KEY_ENV = TASK_API_KEY_ENV


def resolve_task_worker_python(project_root: str, platform_name: str | None = None) -> str:
    """Return the project virtual-environment interpreter for task workers."""
    current_platform = str(platform_name or sys.platform).lower()
    if current_platform == "win32":
        executable = os.path.join(project_root, ".venv-win", "Scripts", "python.exe")
    else:
        executable = os.path.join(project_root, ".venv", "bin", "python")

    if not os.path.isfile(executable):
        raise FileNotFoundError(f"Task worker Python environment not found: {executable}")
    return executable


class TaskManager:
    """A singleton class to manage the CLI task execution state."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):  # Prevent re-initialization
            self.process: Optional[subprocess.Popen] = None
            self.status: str = "idle"  # idle, running, stopping, completed, error
            self.logs = collections.deque(maxlen=500)
            self.chart_data = collections.deque(maxlen=60) # 1 min history at 1s intervals
            self.stats: Dict[str, Any] = self._get_initial_stats()
            self.initialized = True
            self.current_source = ""      # 当前批次原文
            self.current_translation = "" # 当前批次译文
            self.api_url = "http://127.0.0.1" # 默认地址（启动后会带端口）
            
            self.internal_api_url = "http://127.0.0.1" # Worker callback URL（启动后会带端口）
            self.comparison_seq = 0
            self.comparison_updated_at = 0.0

            # Use a separate thread to monitor the process output
            self.monitor_thread: Optional[threading.Thread] = None

    def _get_initial_stats(self) -> Dict[str, Any]:
        return {
            "rpm": 0, "tpm": 0, "totalProgress": 0, "completedProgress": 0,
            "totalTokens": 0, "elapsedTime": 0, "status": "idle",
            "currentFile": "N/A", "successRate": 0, "errorRate": 0
        }

    def push_log(self, message: str, type: str = "info"):
        """Directly push a log message from the host process."""
        self.logs.append({"timestamp": time.time(), "message": message, "type": type})

    def push_comparison(self, source: str, translation: str):
        """Update the side-by-side comparison data from the host process."""
        self.current_source = source
        self.current_translation = translation
        self.comparison_seq += 1
        self.comparison_updated_at = time.time()

    def reset_comparison(self):
        """Clear comparison state for a brand-new task run."""
        self.current_source = ""
        self.current_translation = ""
        self.comparison_seq = 0
        self.comparison_updated_at = 0.0

    def push_stats(self, stats: Dict[str, Any]):
        """Directly push stats from the host process."""
        self.stats.update(stats)
        # Also update chart data
        self.chart_data.append({
            "time": time.strftime('%H:%M:%S'),
            "rpm": self.stats.get("rpm", 0),
            "tpm": self.stats.get("tpm", 0)
        })

    def snapshot_status(self, log_cursor: int = 0, chart_cursor: int = 0, comparison_cursor: int = 0) -> Dict[str, Any]:
        """Get task status snapshot with optional incremental payloads."""
        logs = list(self.logs)
        chart = list(self.chart_data)

        if log_cursor < 0 or log_cursor > len(logs):
            log_cursor = 0
        if chart_cursor < 0 or chart_cursor > len(chart):
            chart_cursor = 0
        if comparison_cursor < 0 or comparison_cursor > self.comparison_seq:
            comparison_cursor = 0

        comparison_changed = self.comparison_seq > comparison_cursor

        return {
            "stats": self.stats,
            "logs": logs[log_cursor:],
            "chart_data": chart[chart_cursor:],
            "comparison": {
                "source": self.current_source,
                "translation": self.current_translation
            } if comparison_changed else None,
            "cursors": {
                "logs": len(logs),
                "chart": len(chart),
                "comparison": self.comparison_seq,
            },
            "comparison_updated_at": self.comparison_updated_at,
        }

    def _log_and_parse(self, stream):
        """Read from a stream, log the output, and parse for stats."""
        # The stream provides correctly decoded strings because of the `encoding` setting in Popen
        for line in iter(stream.readline, ''):
            line = line.strip()
            if not line:
                continue

            # Check for our special stats line
            if line.startswith("[STATS]"):
                try:
                    # Example: [STATS] RPM: 0.00 | TPM: 0.00k | Progress: 0/1435 | Tokens: 0
                    parts = line.split('|')
                    rpm_part = parts[0].split(':')[1].strip()
                    tpm_part = parts[1].split(':')[1].strip().replace('k', '')
                    progress_part = parts[2].split(':')[1].strip()
                    tokens_part = parts[3].split(':')[1].strip()

                    completed, total = map(int, progress_part.split('/'))

                    self.stats["rpm"] = float(rpm_part)
                    self.stats["tpm"] = float(tpm_part) # This is already in k
                    self.stats["completedProgress"] = completed
                    self.stats["totalProgress"] = total
                    self.stats["totalTokens"] = int(tokens_part)
                except (IndexError, ValueError) as e:
                    # Log parsing error if the format is unexpected, but don't crash
                    self.push_log(f"[PARSER_ERROR] Could not parse stats line: {line}. Error: {e}", "warning")
            else:
                # It's a regular log line
                self.push_log(line)


    def start_task(self, payload: TaskSpec | Dict[str, Any]) -> bool:
        """Starts the ainiee_cli.py script as a subprocess with config overrides."""
        with self._lock:
            if self.status in {"running", "stopping"} or (
                self.process is not None and self.process.poll() is None
            ):
                return False
            
            self.status = "running"
            self.logs.clear()
            self.chart_data.clear()
            self.reset_comparison()
            self.stats = self._get_initial_stats()
            self.stats["status"] = "running"
            self.push_log("Task starting with parameters from web UI...")

            try:
                worker_python = resolve_task_worker_python(PROJECT_ROOT)
            except FileNotFoundError as exc:
                self.status = "error"
                self.stats["status"] = "error"
                self.push_log(str(exc), "error")
                return False

            try:
                spec = payload if isinstance(payload, TaskSpec) else TaskSpec.from_mapping(payload)
            except TaskContractError as exc:
                self.status = "error"
                self.stats["status"] = "error"
                self.push_log(f"Invalid task payload: {exc}", "error")
                return False

            cli_args = [
                worker_python,
                os.path.join(PROJECT_ROOT, "ainiee_cli.py"),
                *build_cli_args(spec, non_interactive=True, web_mode=True),
            ]

            try:
                # Get the system's preferred console encoding (e.g., 'gbk' on Chinese Windows)
                system_encoding = locale.getpreferredencoding(False)

                # 注入环境变量以便子进程知道 WebServer 的内部接口位置
                import os as system_os
                env = system_os.environ.copy()
                # Keep credentials out of argv, which is commonly visible in process listings.
                env.pop(WEB_TASK_API_KEY_ENV, None)
                if spec.api_key:
                    env[WEB_TASK_API_KEY_ENV] = spec.api_key
                # 获取当前 WebServer 的运行地址
                env["AINIEE_INTERNAL_API_URL"] = task_manager.internal_api_url
                env[INTERNAL_AUTH_ENV] = INTERNAL_API_TOKEN
                # 强制子进程使用 UTF-8 编码输出，防止在 Windows 下产生编码冲突
                env["PYTHONIOENCODING"] = "utf-8"
                # 标记该进程为后端 Worker，与核心主进程（WebServer）区分
                env["AINIEE_BACKEND_WORKER"] = "1"

                self.process = subprocess.Popen(
                    cli_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    errors='replace', # 增加解码容错，防止非法字符导致线程崩溃
                    bufsize=1,
                    cwd=PROJECT_ROOT,
                    env=env # 传递环境变量
                )
                
                self.monitor_thread = threading.Thread(
                    target=self._process_monitor,
                    args=(self.process,),
                )
                self.monitor_thread.daemon = True
                self.monitor_thread.start()

                return True
            except Exception as e:
                self.status = "error"
                self.stats["status"] = "error"
                self.push_log(f"Failed to start process: {e}", "error")
                return False

    def _process_monitor(self, process=None):
        """Monitors the subprocess, which now provides correctly decoded strings."""
        process = process or self.process
        if process and process.stdout:
            import re
            # Popen now handles the decoding, so we can iterate over strings directly.
            for line in iter(process.stdout.readline, ''):
                line = line.strip()
                if line:
                    self.push_log(line)
                    
                    # 1. Parsing current file
                    if "File:" in line:
                        try: self.stats["currentFile"] = line.split("File:")[1].strip().split("|")[0].strip()
                        except: pass
                    
                    # 2. Parsing [STATS] line (Robust Regex)
                    if "[STATS]" in line:
                        try:
                            # RPM
                            rpm_match = re.search(r"RPM:\s*([\d\.]+)", line)
                            if rpm_match: self.stats["rpm"] = float(rpm_match.group(1))
                            
                            # TPM
                            tpm_match = re.search(r"TPM:\s*([\d\.]+k?)", line)
                            if tpm_match: 
                                tpm_val = tpm_match.group(1).replace('k', '')
                                self.stats["tpm"] = float(tpm_val)
                            
                            # Progress (Completed/Total)
                            prog_match = re.search(r"Progress:\s*(\d+)/(\d+)", line)
                            if prog_match:
                                self.stats["completedProgress"] = int(prog_match.group(1))
                                self.stats["totalProgress"] = int(prog_match.group(2))
                            
                            # Tokens
                            tokens_match = re.search(r"Tokens:\s*(\d+)", line)
                            if tokens_match: self.stats["totalTokens"] = int(tokens_match.group(1))

                            # Success/Error Rate
                            s_rate_match = re.search(r"S-Rate:\s*([\d\.]+)%", line)
                            if s_rate_match: self.stats["successRate"] = float(s_rate_match.group(1))
                            
                            e_rate_match = re.search(r"E-Rate:\s*([\d\.]+)%", line)
                            if e_rate_match: self.stats["errorRate"] = float(e_rate_match.group(1))
                            
                            # Append to Chart Data
                            self.chart_data.append({
                                "time": time.strftime('%H:%M:%S'),
                                "rpm": self.stats["rpm"],
                                "tpm": self.stats["tpm"]
                            })
                            
                        except Exception as e:
                            # Non-fatal parsing error
                            pass
        
        if process:
            process.wait()

        with self._lock:
            if self.process is not process:
                return
            if self.status == "running":
                if process and process.returncode == 0:
                    self.status = "completed"
                    self.stats["status"] = "completed"
                else:
                    self.status = "error"
                    self.stats["status"] = "error"
            elif self.status == "stopping":
                self.status = "idle"
                self.stats["status"] = "idle"
                self.push_log("Task stopped.")
            self.process = None

    def stop_task(self):
        """Stops the running task."""
        with self._lock:
            if self.status not in {"running", "stopping"} or not self.process:
                return True
            
            self.status = "stopping"
            self.stats["status"] = "stopping"
            self.push_log("Sending force stop signal...", "warning")
            
            process = self.process
            try:
                # Direct force kill as requested (Data safety guaranteed by cache)
                process.kill()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.push_log(
                    "Task process did not exit after the stop signal; new tasks remain blocked.",
                    "error",
                )
                return False
            except Exception as e:
                self.push_log(f"Force stop error: {e}", "error")
                if process.poll() is None:
                    return False

            self.process = None
            self.status = "idle"
            self.stats["status"] = "idle"
            self.push_log("Task stopped.")
            return True


task_manager = TaskManager()

# --- Global System Mode ---
# monitor: Only monitoring is allowed
# full: Full control (default)
SYSTEM_MODE = "full"

# --- Simple In-Memory Caches for API Endpoints ---
_version_cache: Dict[str, Any] = {}
_config_cache: Dict[str, Any] = {}
_web_i18n_cache: Dict[str, Dict[str, str]] = {}
_profiles_cache: Optional[List[str]] = None

# --- Profile Handlers (Dependency Injection) ---
# Allows the host application (ainiee_cli.py) to override logic
profile_handlers: Dict[str, Any] = {
    "create": None,
    "rename": None,
    "delete": None
}

# --- Queue Handlers (Dependency Injection) ---
# Queue execution must be delegated to the host CLI instance.
queue_handlers: Dict[str, Any] = {
    "run": None
}

# --- Pydantic Models for API Requests ---

class AppConfig(BaseModel):
    # This needs to match the structure of the config JSON files
    # Define a few key fields for demonstration
    source_language: Optional[str] = None
    target_language: Optional[str] = None
    actual_thread_counts: Optional[int] = None
    temp_file_limit: Optional[int] = 10
    cache_editor_page_size: Optional[int] = 15
    # Add other fields from your config...
    class Config:
        extra = 'allow' # Allow extra fields not defined here

class ProfileSwitchRequest(BaseModel):
    profile: str

class RulesProfileSwitchRequest(BaseModel):
    profile: str

class RulesProfileDeleteRequest(BaseModel):
    profile: str

class ProfileCreateRequest(BaseModel):
    name: str
    base: Optional[str] = None

class ProfileRenameRequest(BaseModel):
    old_name: str
    new_name: str

class ProfileDeleteRequest(BaseModel):
    profile: str

class GlossaryItem(BaseModel):
    src: str
    dst: str
    info: Optional[str] = None
    class Config:
        extra = 'allow'

class TermOption(BaseModel):
    dst: str
    info: str

class TermRetryRequest(BaseModel):
    src: str
    type: str
    avoid: List[str]
    analysis_info: Optional[str] = None
    temp_config: Optional[Dict[str, Any]] = None

class ExclusionItem(BaseModel):
    markers: str
    info: Optional[str] = None
    regex: Optional[str] = None
    class Config:
        extra = 'allow'

class ReplacementRuleItem(BaseModel):
    src: Optional[str] = ""
    dst: Optional[str] = ""
    regex: Optional[str] = ""
    info: Optional[str] = None
    class Config:
        extra = 'allow'

class CharacterizationItem(BaseModel):
    original_name: str
    translated_name: str
    aliases: Optional[List[str]] = []
    gender: Optional[str] = ""
    age: Optional[str] = ""
    personality: Optional[str] = ""
    speech_style: Optional[str] = ""
    pronouns: Optional[str] = ""
    speech_quirks: Optional[str] = ""
    additional_info: Optional[str] = ""
    class Config:
        extra = 'allow'

class TranslationExampleItem(BaseModel):
    src: str
    dst: str
    class Config:
        extra = 'allow'

class StringContent(BaseModel):
    content: str

class PluginEnableRequest(BaseModel):
    name: str
    enabled: bool

class DeleteFileRequest(BaseModel):
    files: List[str]

class QueueTaskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: str | int
    input_path: str
    output_path: Optional[str] = None
    profile: Optional[str] = None
    rules_profile: Optional[str] = None
    source_lang: Optional[str] = None
    target_lang: Optional[str] = None
    project_type: Optional[str] = None
    platform: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    threads: Optional[int] = None
    retry: Optional[int] = None
    timeout: Optional[int] = None
    rounds: Optional[int] = None
    pre_lines: Optional[int] = None
    lines_limit: Optional[int] = None
    tokens_limit: Optional[int] = None
    lines: Optional[int] = None
    tokens: Optional[int] = None
    think_depth: Optional[str | int] = None
    thinking_budget: Optional[int] = None
    failover: Optional[bool] = None
    resume: Optional[bool] = False
    polish_mode: Optional[str] = None
    status: Optional[str] = "waiting"

    def to_task_spec(self) -> TaskSpec:
        payload = self.model_dump(exclude={"status"}, exclude_none=True)
        return TaskSpec.from_mapping(payload)


class QueueTaskUpdate(BaseModel):
    """队列更新输入；显式 null 用于清空可选覆盖。"""

    model_config = ConfigDict(extra="forbid")

    task_type: Optional[str | int] = None
    input_path: Optional[str] = None
    output_path: Optional[str] = None
    profile: Optional[str] = None
    rules_profile: Optional[str] = None
    source_lang: Optional[str] = None
    target_lang: Optional[str] = None
    project_type: Optional[str] = None
    platform: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    threads: Optional[int] = None
    retry: Optional[int] = None
    timeout: Optional[int] = None
    rounds: Optional[int] = None
    pre_lines: Optional[int] = None
    lines_limit: Optional[int] = None
    tokens_limit: Optional[int] = None
    lines: Optional[int] = None
    tokens: Optional[int] = None
    think_depth: Optional[str | int] = None
    thinking_budget: Optional[int] = None
    failover: Optional[bool] = None
    resume: Optional[bool] = None
    polish_mode: Optional[str] = None

class QueueMoveRequest(BaseModel):
    to_index: int

class QueueReorderRequest(BaseModel):
    new_order: List[int]

class QueueRawRequest(BaseModel):
    content: str

class TaskPayload(BaseModel):
    """Web 任务传输模型；执行语义由 TaskSpec 统一归一化。"""

    model_config = ConfigDict(extra="forbid")

    task: Optional[str | int] = None
    task_type: Optional[str | int] = None
    run_all_in_one: Optional[bool] = None
    input_path: Optional[str] = None
    output_path: Optional[str] = None
    project_type: Optional[str] = None
    resume: Optional[bool] = False
    profile: Optional[str] = None
    rules_profile: Optional[str] = None
    queue_file: Optional[str] = None
    
    # Overrides
    source_lang: Optional[str] = None
    target_lang: Optional[str] = None
    threads: Optional[int] = None
    retry: Optional[int] = None
    timeout: Optional[int] = None
    rounds: Optional[int] = None
    pre_lines: Optional[int] = None
    
    # Platform Overrides
    platform: Optional[str] = None
    model: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    failover: Optional[bool] = None
    think_depth: Optional[str | int] = None
    thinking_budget: Optional[int] = None
    
    # Limits
    lines: Optional[int] = None
    tokens: Optional[int] = None
    lines_limit: Optional[int] = None
    tokens_limit: Optional[int] = None
    polish_mode: Optional[str] = None
    manga: Optional[bool] = False

    @model_validator(mode="after")
    def validate_task_contract(self):
        TaskSpec.from_mapping(self.model_dump(exclude_none=True))
        return self

    def to_task_spec(self) -> TaskSpec:
        return TaskSpec.from_mapping(self.model_dump(exclude_none=True))

# --- FastAPI Application ---

app = FastAPI(title="AiNiee CLI Backend API")
app.include_router(manga_router)

# --- Paths to Resources ---
RESOURCE_PATH = os.path.join(PROJECT_ROOT, "Resource")
VERSION_FILE = os.path.join(RESOURCE_PATH, "Version", "version.json")
PROFILES_PATH = os.path.join(RESOURCE_PATH, "profiles")
RULES_PROFILES_PATH = os.path.join(RESOURCE_PATH, "rules_profiles")
ROOT_CONFIG_FILE = os.path.join(RESOURCE_PATH, "config.json")
PRESET_PATH = os.path.join(RESOURCE_PATH, "platforms", "preset.json")
WEB_SERVER_PATH = os.path.join(PROJECT_ROOT, "Tools", "WebServer")
WEB_SESSION_TOKEN = os.environ.get("AINIEE_WEB_SESSION_TOKEN", "") or secrets.token_urlsafe(32)
MCP_AUTH_TOKEN = os.environ.get("AINIEE_MCP_AUTH_TOKEN", "")
INTERNAL_API_TOKEN = os.environ.get(INTERNAL_AUTH_ENV, "") or secrets.token_urlsafe(32)
REMOTE_ACCESS_ENABLED = False
PUBLIC_API_ROUTES = {
    ("POST", "/api/session/bootstrap"),
    ("GET", "/api/version"),
    ("GET", "/api/system/mode"),
}

# --- Helper Functions ---


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _is_loopback_bind_host(host: str) -> bool:
    return is_loopback_bind_host(host)


def _is_internal_api_path(path: str) -> bool:
    return path == "/api/internal" or path.startswith("/api/internal/")


def _is_public_api_request(request: Request) -> bool:
    if request.method.upper() == "OPTIONS":
        return True
    return (request.method.upper(), request.url.path) in PUBLIC_API_ROUTES


def _has_valid_web_session(request: Request) -> bool:
    token = request.cookies.get(WEB_SESSION_COOKIE_NAME, "")
    return bool(token) and secrets.compare_digest(token, WEB_SESSION_TOKEN)


def _has_valid_mcp_auth(request: Request) -> bool:
    if not is_mcp_request(request):
        return False
    if not MCP_AUTH_TOKEN:
        return False
    token = request.headers.get(MCP_AUTH_HEADER, "")
    return bool(token) and secrets.compare_digest(token, MCP_AUTH_TOKEN)


def _has_valid_internal_auth(request: Request) -> bool:
    token = request.headers.get(INTERNAL_AUTH_HEADER, "")
    return bool(token) and secrets.compare_digest(token, INTERNAL_API_TOKEN)


def _ensure_api_access(request: Request) -> None:
    """Require an authenticated browser session or MCP bridge for protected APIs."""
    if _has_valid_web_session(request) or _has_valid_mcp_auth(request):
        return

    raise HTTPException(
        status_code=403,
        detail=(
            "API access requires a valid Web UI session or MCP bridge token. "
            "Direct unauthenticated HTTP bypass is not allowed."
        ),
    )


@app.middleware("http")
async def web_session_middleware(request: Request, call_next):
    """Guard APIs by default and leave only explicitly public routes unauthenticated."""
    path = request.url.path
    if _is_api_path(path) and not _is_public_api_request(request):
        if _is_internal_api_path(path):
            if not _has_valid_internal_auth(request):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Internal API access requires a valid worker token."},
                )
        else:
            try:
                _ensure_api_access(request)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                    headers={AUTH_REQUIRED_HEADER: WEB_SESSION_AUTH_REQUIRED},
                )

    response = await call_next(request)

    if not _is_api_path(path):
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            # WebView may reuse a cached SPA document without contacting the restarted backend.
            response.headers["Cache-Control"] = "no-cache"

    return response


@app.post("/api/session/bootstrap", status_code=204)
async def bootstrap_web_session(request: Request, response: Response):
    """Establish the short-lived browser channel before accessing sensitive APIs."""
    origin = request.headers.get("origin", "").rstrip("/")
    expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}".rstrip("/")
    if (
        (not REMOTE_ACCESS_ENABLED and not _is_loopback_bind_host(request.url.hostname or ""))
        or not origin
        or origin != expected_origin
    ):
        raise HTTPException(status_code=403, detail="Web UI session bootstrap requires a same-origin request.")
    response.set_cookie(
        key=WEB_SESSION_COOKIE_NAME,
        value=WEB_SESSION_TOKEN,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )

def get_config_mode():
    """Checks if the config is in 'profile' mode or 'legacy' single-file mode."""
    return "profile", load_root_config()

def get_active_profile_path() -> str:
    """Gets the full path to the active profile JSON file."""
    _, config = get_config_mode()
    profile_name = config.get("active_profile", "default")
    profile_path, _ = resolve_profile_path(PROFILES_PATH, profile_name)
    return profile_path

def _load_web_i18n_data(lang: str) -> Dict[str, str]:
    normalized = lang or "zh_CN"
    if normalized not in ("zh_CN", "zh_CNTW", "en", "ja", "ko", "ru", "es"):
        normalized = "zh_CN" if str(normalized).lower().startswith("zh") else "en"
    if normalized in _web_i18n_cache:
        return _web_i18n_cache[normalized]

    path = os.path.join(PROJECT_ROOT, "I18N", f"{normalized}.json")
    data: Dict[str, str] = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        except Exception:
            data = {}
    if not data and normalized != "en":
        return _load_web_i18n_data("en")

    _web_i18n_cache[normalized] = data
    return data

def _get_web_i18n_lang(config: Optional[Dict[str, Any]] = None) -> str:
    if config and config.get("interface_language"):
        return config.get("interface_language")

    mode, root_config = get_config_mode()
    if root_config.get("interface_language"):
        return root_config.get("interface_language")

    profile_path = get_active_profile_path()
    if os.path.exists(profile_path):
        try:
            with open(profile_path, 'r', encoding='utf-8-sig') as f:
                profile_config = json.load(f)
            if profile_config.get("interface_language"):
                return profile_config.get("interface_language")
        except Exception:
            pass
    return "zh_CN"

def _web_tr(key: str, default: Optional[str] = None, *args, lang: Optional[str] = None,
            config: Optional[Dict[str, Any]] = None) -> str:
    resolved_lang = lang or _get_web_i18n_lang(config)
    data = _load_web_i18n_data(resolved_lang)
    value = data.get(key)
    if (not value or value == key) and resolved_lang != "en":
        value = _load_web_i18n_data("en").get(key)
    if not value or value == key:
        value = default if default is not None else key
    if args:
        try:
            return value.format(*args)
        except Exception:
            return value
    return value

def get_active_rules_profile_path() -> str:
    """Gets the full path to the active rules profile JSON file."""
    _, root_config = get_config_mode()
    rules_profile = root_config.get("active_rules_profile", "default")
    os.makedirs(RULES_PROFILES_PATH, exist_ok=True)
    rules_path, _ = resolve_profile_path(RULES_PROFILES_PATH, rules_profile, allow_none=True)
    return rules_path


def _ensure_no_mcp_secret_placeholder(data: Any, context: str):
    """Reject writes that still contain unresolved MCP redacted placeholders."""
    if contains_redacted_secret(data):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{context} still contains MCP redacted secret placeholders. "
                "Ask the user to provide a new secret explicitly before saving."
            ),
        )

def save_rule_generic(key: str, value: Any):
    """Helper to save a specific rule key to the active RULES profile."""
    global _config_cache
    try:
        if key not in RULE_PROFILE_KEYS:
            raise ValueError(f"{key} is not a rules profile key")
        save_rule_value(key, value)
        _config_cache.clear()
        return True
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save rule {key}: {e}")

def save_config_generic(key: str, value: Any):
    """Helper to save a specific key to the active SETTINGS profile."""
    global _config_cache
    try:
        if key in RULE_PROFILE_KEYS:
            save_rule_value(key, value)
        else:
            save_setting_value(key, value)
        _config_cache.clear()
        return True
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save setting {key}: {e}")

def _new_replacement_rule_id() -> str:
    return f"rr_{secrets.token_urlsafe(12)}"

def _normalize_replacement_rule_items(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []

    normalized: List[Dict[str, Any]] = []
    seen_ids = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        next_item.pop("_serverIndex", None)
        item_id = str(next_item.get("_id") or "").strip()
        if not item_id or item_id in seen_ids:
            item_id = _new_replacement_rule_id()
            while item_id in seen_ids:
                item_id = _new_replacement_rule_id()
        next_item["_id"] = item_id
        seen_ids.add(item_id)
        normalized.append(next_item)
    return normalized

def delete_rule_item_generic(key: str, index: int):
    config = _load_active_config_payload()
    items = config.get(key, [])
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail=f"{key} is not a list rule")
    if index < 0 or index >= len(items):
        raise HTTPException(status_code=404, detail="Rule item not found")
    next_items = list(items)
    deleted = next_items.pop(index)
    save_rule_generic(key, next_items)
    return {"message": "Rule item deleted.", "deleted": deleted, "deleted_index": index, "items": next_items}

def delete_rule_item_by_id_generic(key: str, item_id: str):
    config = _load_active_config_payload()
    items = config.get(key, [])
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail=f"{key} is not a list rule")

    for index, item in enumerate(items):
        if isinstance(item, dict) and str(item.get("_id") or "") == item_id:
            next_items = list(items)
            deleted = next_items.pop(index)
            save_rule_generic(key, next_items)
            return {
                "message": "Rule item deleted.",
                "deleted": deleted,
                "deleted_index": index,
                "items": next_items,
            }

    raise HTTPException(status_code=404, detail="Rule item not found")

def _file_cache_token(path: str) -> tuple:
    """Return a cheap freshness token for config/profile files."""
    try:
        stat = os.stat(path)
        return (path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (path, None, None)

def _active_config_cache_key(root_config: Dict[str, Any]) -> tuple:
    current_profile_name = root_config.get("active_profile", "default")
    current_rules_name = root_config.get("active_rules_profile", "default")
    profile_path, _ = resolve_profile_path(PROFILES_PATH, current_profile_name)
    rules_path = ""
    if current_rules_name != "None":
        rules_path, _ = resolve_profile_path(RULES_PROFILES_PATH, current_rules_name, allow_none=True)

    return (
        current_profile_name,
        current_rules_name,
        _file_cache_token(ROOT_CONFIG_FILE),
        _file_cache_token(PRESET_PATH),
        _file_cache_token(profile_path),
        _file_cache_token(rules_path) if rules_path else ("None", None, None),
    )

# --- API Endpoints ---

@app.get("/api/system/mode")
async def get_system_mode():
    return {"mode": SYSTEM_MODE}

@app.get("/api/version")
async def get_version():
    global _version_cache
    if "version" in _version_cache:
        return _version_cache["version"]

    if not os.path.exists(VERSION_FILE):
        # Fallback to a default if file is missing
        return {"version": "V0.0.0 (Version file not found)"}
        
    try:
        with open(VERSION_FILE, 'r', encoding='utf-8') as f:
            version_data = json.load(f)
            _version_cache["version"] = version_data
            return version_data
    except:
        return {"version": "V0.0.0 (Read Error)"}

@app.post("/api/config")
async def save_config(config: AppConfig, request: Request):
    """
    Saves the provided JSON to the active settings profile, rules profile, and root config.
    """
    global _config_cache

    try:
        config_dict = config.model_dump(exclude_unset=True) if hasattr(config, 'model_dump') else config.dict(exclude_unset=True)
        config_dict = strip_mcp_security_metadata(config_dict)
        current_config = dict(_load_active_config_payload())
        remote_access_value = current_config.get(REMOTE_ACCESS_CONFIG_KEY) is True

        # MCP 读取配置时会看到脱敏后的占位符，这里写回前要恢复当前已保存的真实密钥。
        if is_mcp_request(request):
            config_dict = restore_redacted_secrets(config_dict, current_config)
            _ensure_no_mcp_secret_placeholder(config_dict, "Config payload")

        current_config.update(config_dict)
        # This advanced security boundary is intentionally editable only from TUI.
        current_config[REMOTE_ACCESS_CONFIG_KEY] = remote_access_value
        prefer_sdk_request_mode = "sdk_request_mode" in config_dict
        sync_sdk_request_mode_config(current_config, prefer_sdk_request_mode=prefer_sdk_request_mode)
        save_effective_config(current_config, prefer_sdk_request_mode=prefer_sdk_request_mode)
        _config_cache.clear()
        return {"message": "Config saved successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write to config file: {e}")

def _load_active_config_payload() -> Dict[str, Any]:
    """
    Load the active settings profile and rules profile into one merged payload.

    This helper is intentionally request-agnostic so internal Web handlers can
    reuse the merged config without needing to fake an HTTP Request object.
    """
    global _config_cache

    _, root_config = get_config_mode()
    cache_key = _active_config_cache_key(root_config)
    if cache_key in _config_cache:
        return _config_cache[cache_key]
    loaded_config = load_effective_config(root_config=root_config, create_missing=False)

    # 防护：确保 response_check_switch 是正确的 dict 类型
    default_check_switch = {
        "newline_character_count_check": False, "return_to_original_text_check": False,
        "residual_original_text_check": False, "reply_format_check": False
    }
    if "response_check_switch" not in loaded_config or not isinstance(loaded_config.get("response_check_switch"), dict):
        loaded_config["response_check_switch"] = default_check_switch

    _config_cache[cache_key] = loaded_config
    return loaded_config


def _is_prompt_selection_valid(selection: Any, builtin_ids: set[int]) -> bool:
    if not isinstance(selection, dict):
        return False
    selected_id = selection.get("last_selected_id")
    if selected_id in builtin_ids:
        return True
    prompt_content = selection.get("prompt_content")
    return selected_id not in (None, "") and isinstance(prompt_content, str) and bool(prompt_content.strip())


def _missing_prompt_selections(task: str, config: Dict[str, Any]) -> List[str]:
    task_name = str(task or "").lower()
    missing = []
    if task_name in ("translate", "translation", "all_in_one", "translate_and_polish"):
        if not _is_prompt_selection_valid(config.get("translation_prompt_selection"), {100, 200, 300}):
            missing.append("translation")
    if task_name in ("polish", "polishing", "all_in_one", "translate_and_polish"):
        if not _is_prompt_selection_valid(config.get("polishing_prompt_selection"), {10001}):
            missing.append("polishing")
    return missing


@app.get("/api/config")
async def get_config(request: Request):
    """
    Returns the content of the active configuration merged with active rules.
    """
    loaded_config = _load_active_config_payload()

    if is_mcp_request(request):
        return sanitize_data_for_mcp(loaded_config, path="/api/config")

    return loaded_config
@app.get("/api/glossary")
async def get_glossary():
    config = _load_active_config_payload()
    return config.get("prompt_dictionary_data", [])

@app.post("/api/glossary")
async def save_glossary(items: List[Dict[str, Any]]):
    save_rule_generic("prompt_dictionary_data", items)
    return {"message": "Glossary saved successfully."}

@app.post("/api/glossary/add")
async def add_glossary_item(item: GlossaryItem):
    current = await get_glossary()
    # Check if exists - current items may be dicts
    found = False
    for i, existing in enumerate(current):
        existing_src = existing.src if hasattr(existing, 'src') else existing.get('src', '')
        if existing_src == item.src:
            current[i] = item
            found = True
            break

    if not found:
        current.append(item)

    save_rule_generic("prompt_dictionary_data", [i.dict() if hasattr(i, 'dict') else i for i in current])
    return {"message": "Term added to glossary."}

@app.post("/api/glossary/batch-add")
async def batch_add_glossary_items(request: Dict[str, List[GlossaryItem]]):
    items = request.get("terms", [])
    current = await get_glossary()

    # Build map from current glossary (handle both dict and GlossaryItem)
    current_map = {}
    for it in current:
        if isinstance(it, dict):
            current_map[it.get('src', '')] = it
        else:
            current_map[it.src] = it

    # Add/update items (items from request are dicts)
    for item in items:
        if isinstance(item, dict):
            src = item.get('src', '')
            current_map[src] = item
        else:
            current_map[item.src] = item.dict() if hasattr(item, 'dict') else item

    # Save all as dicts
    save_rule_generic("prompt_dictionary_data", list(current_map.values()))
    return {"message": f"Successfully added {len(items)} terms."}

@app.post("/api/term/retry")
async def retry_term_translation(request: TermRetryRequest):
    try:
        from ModuleFolders.Infrastructure.LLMRequester.LLMRequester import LLMRequester
        from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
        from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType

        # 1. Load configuration
        config = _load_active_config_payload()
        task_config = TaskConfig()
        task_config.load_config_from_dict(config)
        
        # 2. Handle temporary overrides if provided
        if request.temp_config:
            platform_name = request.temp_config.get("platform")
            if platform_name:
                # Ensure the platform exists in config
                if platform_name not in task_config.platforms:
                    # Create a default structure if it's a new platform tag
                    task_config.platforms[platform_name] = {
                        "tag": platform_name,
                        "name": platform_name,
                        "group": "custom",
                        "api_format": "OpenAI"
                    }
                
                # Update specific fields
                plat_ref = task_config.platforms[platform_name]
                if request.temp_config.get("api_key"): plat_ref["api_key"] = request.temp_config["api_key"]
                if request.temp_config.get("api_url"): plat_ref["api_url"] = request.temp_config["api_url"]
                if request.temp_config.get("model"): plat_ref["model"] = request.temp_config["model"]
                
                # Set as active platform for this request
                task_config.api_settings["translate"] = platform_name

        # 3. Prepare task config (this handles model normalization, URL completion, API key rotation)
        task_config.prepare_for_translation(TaskType.TRANSLATION)
        platform_config = task_config.get_platform_configuration("translationReq")
        target_language = task_config.target_language
        
        # 4. Construct Prompt (Match ainiee_cli.py logic)
        term_type = request.type or "专有名词"
        analysis_info = request.analysis_info or "null"
        avoid_hint = ""
        if request.avoid:
            avoid_list = ", ".join(request.avoid[:5])
            avoid_hint = f"\nPlease provide a different translation from: {avoid_list}"

        system_prompt = f"""You are a terminology translator. Translate the term into "{target_language}".
Term type: {term_type}
Known context: {analysis_info}
{avoid_hint}

Translation is the exact replacement value that may be inserted into translated text. It must contain only the final translated surface form.
Never output "translation (note)", "translation / note", "translation: note", "translation - note", or any other "translation + explanation" form in Translation.
Never put identity, role labels, character relationships, context notes, disambiguation, jokes, or reader-facing hints in Translation; put them in Note.
Qualifiers such as left/right/front/back or numbers may appear in Translation only when explicitly present in the source term. If no pure replacement-safe translation is certain, leave Translation empty.

Output format (use | as separator):
Translation|Note"""

        messages = [{"role": "user", "content": request.src}]

        # 5. Execute Request
        requester = LLMRequester()
        skip, _, response, _, _ = requester.sent_request(messages, system_prompt, platform_config)
        
        if skip or not response:
            raise HTTPException(status_code=500, detail="LLM request failed or was skipped")
            
        # 6. Parse Response (Match ainiee_cli.py logic)
        response_text = response.strip()
        if '|' in response_text:
            parts = response_text.split('|', 1)
            dst = parts[0].strip()
            info = parts[1].strip() if len(parts) > 1 else ""
        else:
            dst = response_text
            info = ""
            
        # Post-process dst
        if dst.startswith(("Translation:", "译文:", "译文：")):
            dst = dst.split(":", 1)[-1].split("：", 1)[-1].strip()
        dst = dst.strip('"').strip("'")
            
        return {"dst": dst, "info": info}
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/exclusion")
async def get_exclusion():
    config = _load_active_config_payload()
    return config.get("exclusion_list_data", [])

@app.post("/api/exclusion")
async def save_exclusion(items: List[Dict[str, Any]]):
    save_rule_generic("exclusion_list_data", items)
    return {"message": "Exclusion list saved successfully."}

@app.get("/api/pre_translation")
async def get_pre_translation():
    config = _load_active_config_payload()
    items = _normalize_replacement_rule_items(config.get("pre_translation_data", []))
    return items

@app.post("/api/pre_translation")
async def save_pre_translation(items: List[Dict[str, Any]]):
    normalized = _normalize_replacement_rule_items(items)
    save_rule_generic("pre_translation_data", normalized)
    return {"message": "Pre-translation rules saved.", "items": normalized}

@app.delete("/api/pre_translation/by-id/{item_id}")
async def delete_pre_translation_item_by_id(item_id: str):
    return delete_rule_item_by_id_generic("pre_translation_data", item_id)

@app.delete("/api/pre_translation/{index}")
async def delete_pre_translation_item(index: int):
    return delete_rule_item_generic("pre_translation_data", index)

@app.get("/api/post_translation")
async def get_post_translation():
    config = _load_active_config_payload()
    items = _normalize_replacement_rule_items(config.get("post_translation_data", []))
    return items

@app.post("/api/post_translation")
async def save_post_translation(items: List[Dict[str, Any]]):
    normalized = _normalize_replacement_rule_items(items)
    save_rule_generic("post_translation_data", normalized)
    return {"message": "Post-translation rules saved.", "items": normalized}

@app.delete("/api/post_translation/by-id/{item_id}")
async def delete_post_translation_item_by_id(item_id: str):
    return delete_rule_item_by_id_generic("post_translation_data", item_id)

@app.delete("/api/post_translation/{index}")
async def delete_post_translation_item(index: int):
    return delete_rule_item_generic("post_translation_data", index)

# --- New Features Endpoints ---

@app.get("/api/characterization")
async def get_characterization():
    config = _load_active_config_payload()
    return config.get("characterization_data", [])

@app.post("/api/characterization")
async def save_characterization(items: List[Dict[str, Any]]):
    save_rule_generic("characterization_data", items)
    return {"message": "Characterization saved."}

@app.get("/api/world_building", response_model=StringContent)
async def get_world_building():
    config = _load_active_config_payload()
    return {"content": config.get("world_building_content", "")}

@app.post("/api/world_building")
async def save_world_building(data: StringContent):
    save_rule_generic("world_building_content", data.content)
    return {"message": "World building saved."}

@app.get("/api/writing_style", response_model=StringContent)
async def get_writing_style():
    config = _load_active_config_payload()
    return {"content": config.get("writing_style_content", "")}

@app.post("/api/writing_style")
async def save_writing_style(data: StringContent):
    save_rule_generic("writing_style_content", data.content)
    return {"message": "Writing style saved."}

@app.get("/api/translation_example")
async def get_translation_example():
    config = _load_active_config_payload()
    return config.get("translation_example_data", [])

@app.post("/api/translation_example")
async def save_translation_example(items: List[Dict[str, Any]]):
    save_rule_generic("translation_example_data", items)
    return {"message": "Translation examples saved."}

# --- AI Glossary Analysis Endpoints ---
DEFAULT_GLOSSARY_TOKEN_WARNING_THRESHOLD = 256_000
DEFAULT_INCREMENTAL_SPLIT_TARGET_TOKENS = 200_000
MAX_INCREMENTAL_SPLIT_TARGET_TOKENS = 256_000

class GlossaryAnalysisRequest(BaseModel):
    input_path: str
    analysis_percent: int = 100
    analysis_lines: Optional[int] = None
    analysis_mode: str = "full"
    incremental_split_target_tokens: Optional[int] = None
    prompt_file: Optional[str] = None
    translate_during_analysis: bool = False
    use_temp_config: bool = False
    temp_platform: Optional[str] = None
    temp_api_key: Optional[str] = None
    temp_api_url: Optional[str] = None
    temp_model: Optional[str] = None
    temp_threads: Optional[int] = None

class GlossaryAnalysisPreflightRequest(BaseModel):
    input_path: str
    analysis_percent: int = 100
    analysis_lines: Optional[int] = None

class GlossaryAnalysisStatus(BaseModel):
    status: str  # 'idle', 'running', 'completed', 'error'
    progress: int = 0
    total: int = 0
    message: str = ""
    results: List[dict] = []

# Global state for analysis task
_analysis_state = {
    "status": "idle",
    "progress": 0,
    "total": 0,
    "message": "",
    "results": [],
    "logs": [],
    "estimated_tokens": 0,
    "analysis_mode": "full",
    "structured_analysis": {},
}

@app.get("/api/glossary/analysis/status")
async def get_analysis_status():
    return _analysis_state

@app.post("/api/glossary/analysis/preflight")
async def preflight_glossary_analysis(request: GlossaryAnalysisPreflightRequest):
    try:
        config = _load_active_config_payload()
        return _build_glossary_analysis_preflight(
            request.input_path,
            request.analysis_percent,
            request.analysis_lines,
            config,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/api/glossary/analysis/start")
async def start_glossary_analysis(request: GlossaryAnalysisRequest):
    global _analysis_state

    if _analysis_state["status"] == "running":
        raise HTTPException(status_code=400, detail="Analysis already running")

    lang = _get_web_i18n_lang()

    # Reset state
    _analysis_state = {
        "status": "running",
        "progress": 0,
        "total": 0,
        "message": _web_tr("glossary_log_initializing", "初始化中...", lang=lang),
        "results": [],
        "logs": [f"[{datetime.now().strftime('%H:%M:%S')}] {_web_tr('glossary_log_start_analysis', '开始分析', lang=lang)}: {request.input_path}"],
        "estimated_tokens": 0,
        "analysis_mode": request.analysis_mode,
        "structured_analysis": {},
        "translate_during_analysis": request.translate_during_analysis,
        "lang": lang,
    }

    # Start analysis in background thread
    import threading
    thread = threading.Thread(
        target=_run_glossary_analysis,
        args=(request.input_path, request.analysis_percent, request.analysis_lines,
              request.analysis_mode, request.prompt_file,
              request.incremental_split_target_tokens,
              request.translate_during_analysis,
              request.use_temp_config, request.temp_platform, request.temp_api_key,
              request.temp_api_url, request.temp_model, request.temp_threads)
    )
    thread.daemon = True
    thread.start()

    return {"message": "Analysis started"}

def _add_analysis_log(message: str):
    """添加分析日志"""
    global _analysis_state
    timestamp = datetime.now().strftime('%H:%M:%S')
    _analysis_state["logs"].append(f"[{timestamp}] {message}")

def _add_analysis_log_i18n(key: str, default: str, *args):
    lang = _analysis_state.get("lang") or _get_web_i18n_lang()
    _add_analysis_log(_web_tr(key, default, *args, lang=lang))

def _estimate_glossary_tokens(text: str) -> int:
    try:
        from ModuleFolders.Infrastructure.Cache.CacheItem import CacheItem
        return CacheItem.get_token_count(text)
    except Exception:
        if not text:
            return 0
        ascii_count = sum(1 for c in text if ord(c) < 128)
        non_ascii_count = len(text) - ascii_count
        return max(1, int(ascii_count / 4 + non_ascii_count / 1.5))

def _get_glossary_token_warning_threshold(config: Dict[str, Any]) -> int:
    try:
        threshold = int(config.get("glossary_analysis_token_warning_threshold") or 0)
    except (TypeError, ValueError):
        threshold = 0
    return threshold if threshold > 0 else DEFAULT_GLOSSARY_TOKEN_WARNING_THRESHOLD

def _get_incremental_split_target_tokens(config: Dict[str, Any], requested: Optional[int] = None) -> int:
    value = requested if requested is not None else config.get("glossary_analysis_incremental_split_target_tokens")
    try:
        target = int(value or 0)
    except (TypeError, ValueError):
        target = 0
    if target <= 0:
        target = DEFAULT_INCREMENTAL_SPLIT_TARGET_TOKENS
    return min(target, MAX_INCREMENTAL_SPLIT_TARGET_TOKENS)

def _normalize_glossary_analysis_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    return normalized if normalized in {"full", "split", "incremental_split"} else "full"

def _build_glossary_analysis_preflight(input_path: str, analysis_percent: int, analysis_lines: Optional[int], config: Dict[str, Any]) -> Dict[str, Any]:
    from ModuleFolders.Domain.FileReader.FileReader import FileReader

    file_reader = FileReader()
    project_type = config.get("translation_project", "auto")
    cache_data = file_reader.read_files(project_type, input_path, "")
    if not cache_data:
        raise HTTPException(status_code=400, detail="Unable to read file content")

    all_items = list(cache_data.items_iter())
    total_lines = len(all_items)
    if total_lines == 0:
        raise HTTPException(status_code=400, detail="No analyzable text found")

    if analysis_lines:
        lines_to_analyze = min(analysis_lines, total_lines)
    else:
        lines_to_analyze = int(total_lines * analysis_percent / 100)
    lines_to_analyze = max(1, lines_to_analyze)

    selected_text = "\n".join([item.source_text for item in all_items[:lines_to_analyze]])
    estimated_tokens = _estimate_glossary_tokens(selected_text)
    warning_threshold = _get_glossary_token_warning_threshold(config)
    recommended_split_target_tokens = _get_incremental_split_target_tokens(config)
    return {
        "total_lines": total_lines,
        "lines_to_analyze": lines_to_analyze,
        "estimated_tokens": estimated_tokens,
        "warning_threshold": warning_threshold,
        "recommended_split_target_tokens": recommended_split_target_tokens,
        "max_split_target_tokens": MAX_INCREMENTAL_SPLIT_TARGET_TOKENS,
        "exceeds_warning": estimated_tokens > warning_threshold,
    }

def _split_glossary_items_by_tokens(items: list, target_tokens: int) -> list:
    batches = []
    current = []
    current_tokens = 0
    target_tokens = max(1, min(int(target_tokens or DEFAULT_INCREMENTAL_SPLIT_TARGET_TOKENS), MAX_INCREMENTAL_SPLIT_TARGET_TOKENS))
    for item in items or []:
        text = getattr(item, "source_text", "")
        item_tokens = max(1, _estimate_glossary_tokens(text))
        if item_tokens > target_tokens:
            if current:
                batches.append(current)
                current = []
                current_tokens = 0
            sentence_chunks = _split_glossary_text_by_sentence_boundaries(text, target_tokens)
            if len(sentence_chunks) > 1:
                for chunk in sentence_chunks:
                    batches.append([_clone_glossary_item_with_text(item, chunk)])
                continue
        if current and current_tokens + item_tokens > target_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(item)
        current_tokens += item_tokens
    if current:
        batches.append(current)
    return batches or [items]

def _split_glossary_text_by_sentence_boundaries(text: str, target_tokens: int) -> list:
    text = _normalize_glossary_text(text)
    if not text:
        return []

    sentences = re.findall(r".+?(?:[。！？!?\.]+[”’\"']?|\n+|$)", text, flags=re.S)
    sentences = [sentence for sentence in (s.strip() for s in sentences) if sentence]
    if len(sentences) <= 1:
        return [text]

    chunks = []
    current = []
    current_tokens = 0
    target_tokens = max(1, min(int(target_tokens or DEFAULT_INCREMENTAL_SPLIT_TARGET_TOKENS), MAX_INCREMENTAL_SPLIT_TARGET_TOKENS))

    for sentence in sentences:
        sentence_tokens = max(1, _estimate_glossary_tokens(sentence))
        if current and current_tokens + sentence_tokens > target_tokens:
            chunks.append("\n".join(current))
            current = []
            current_tokens = 0
        current.append(sentence)
        current_tokens += sentence_tokens

    if current:
        chunks.append("\n".join(current))
    return chunks or [text]

def _clone_glossary_item_with_text(item, text: str):
    class TextOnlyItem:
        def __init__(self, source_text):
            self.source_text = source_text

    try:
        clone = item.__class__.__new__(item.__class__)
        if hasattr(item, "__dict__"):
            clone.__dict__.update(item.__dict__)
        setattr(clone, "source_text", text)
        return clone
    except Exception:
        return TextOnlyItem(text)

def _resolve_glossary_prompt_file(prompt_file: str = None) -> str:
    if prompt_file and os.path.exists(prompt_file):
        return prompt_file

    lang = _get_web_i18n_lang()
    default_prompt = "glossary_extract_zh.txt" if str(lang).startswith("zh") else "glossary_extract_en.txt"
    prompt_file = os.path.join(PROJECT_ROOT, "Resource", "Prompt", "System", default_prompt)
    if not os.path.exists(prompt_file):
        fallback_prompt = "glossary_extract_en.txt" if default_prompt != "glossary_extract_en.txt" else "glossary_extract_zh.txt"
        prompt_file = os.path.join(PROJECT_ROOT, "Resource", "Prompt", "System", fallback_prompt)
    return prompt_file

def _count_glossary_term_occurrences(text: str, term: str) -> int:
    if not text or not term:
        return 0
    return text.count(term)

def _run_glossary_analysis(input_path: str, analysis_percent: int, analysis_lines: Optional[int],
                           analysis_mode: str = "full", prompt_file: str = None,
                           incremental_split_target_tokens: Optional[int] = None,
                           translate_during_analysis: bool = False,
                           use_temp: bool = False, temp_platform: str = None, temp_key: str = None,
                           temp_url: str = None, temp_model: str = None, temp_threads: int = None):
    global _analysis_state

    try:
        from ModuleFolders.Domain.FileReader.FileReader import FileReader
        from ModuleFolders.Infrastructure.LLMRequester.LLMRequester import LLMRequester
        from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
        from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
        import concurrent.futures
        import threading
        import re

        lang = _analysis_state.get("lang") or _get_web_i18n_lang()

        # Load merged config, including active rules profile.
        config = _load_active_config_payload()
        if config.get("interface_language"):
            lang = config.get("interface_language")
            _analysis_state["lang"] = lang

        # Read file
        _analysis_state["message"] = _web_tr("msg_reading_file", "正在读取文件...", lang=lang)
        _add_analysis_log_i18n("msg_reading_file", "正在读取文件...")
        file_reader = FileReader()
        project_type = config.get("translation_project", "auto")
        cache_data = file_reader.read_files(project_type, input_path, "")

        if not cache_data:
            _analysis_state["status"] = "error"
            _analysis_state["message"] = _web_tr("msg_no_content", "无法读取文件内容", lang=lang)
            _add_analysis_log_i18n("glossary_log_error_message", "错误: {}", _web_tr("msg_no_content", "无法读取文件内容", lang=lang))
            return

        all_items = list(cache_data.items_iter())
        total_lines = len(all_items)

        if total_lines == 0:
            _analysis_state["status"] = "error"
            _analysis_state["message"] = _web_tr("msg_no_text_found", "未找到可分析的文本", lang=lang)
            _add_analysis_log_i18n("glossary_log_error_message", "错误: {}", _web_tr("msg_no_text_found", "未找到可分析的文本", lang=lang))
            return

        # Calculate lines to analyze
        if analysis_lines:
            lines_to_analyze = min(analysis_lines, total_lines)
        else:
            lines_to_analyze = int(total_lines * analysis_percent / 100)
        lines_to_analyze = max(1, lines_to_analyze)

        _add_analysis_log_i18n("glossary_log_line_count", "总行数: {}, 将分析: {} 行", total_lines, lines_to_analyze)

        items_to_analyze = all_items[:lines_to_analyze]
        selected_text = "\n".join([item.source_text for item in items_to_analyze])
        estimated_tokens = _estimate_glossary_tokens(selected_text)
        normalized_mode = _normalize_glossary_analysis_mode(analysis_mode)
        _analysis_state["estimated_tokens"] = estimated_tokens
        _analysis_state["analysis_mode"] = normalized_mode
        _add_analysis_log_i18n("glossary_log_estimated_tokens_note", "预估Token: {}（仅供参考，实际仍按行数/比例截取）", estimated_tokens)

        # Load prompt
        prompt_file = _resolve_glossary_prompt_file(prompt_file)
        _add_analysis_log_i18n("glossary_log_prompt_file_value", "提示词文件: {}", prompt_file)

        with open(prompt_file, 'r', encoding='utf-8') as f:
            system_prompt = f.read()

        # Configure request
        task_config = TaskConfig()
        task_config.load_config_from_dict(config)
        task_config.prepare_for_translation(TaskType.TRANSLATION)

        # Use temp config or current config
        if use_temp and temp_platform:
            platform_config = {
                "target_platform": temp_platform,
                "api_key": temp_key or "",
                "api_url": temp_url or "",
                "model": temp_model or ""
            }
            _analysis_state["message"] = f"{_web_tr('msg_using_temp_config', '使用临时API配置', lang=lang)}: {temp_platform}"
            _add_analysis_log_i18n(
                "glossary_log_using_temp_config",
                "使用临时API配置: {}, 模型: {}",
                temp_platform,
                temp_model or _web_tr("glossary_log_default_prompt", "默认", lang=lang)
            )
        else:
            platform_config = task_config.get_platform_configuration("translationReq")
            _add_analysis_log_i18n("glossary_log_using_current_config", "使用当前配置: {}", platform_config.get('target_platform', 'unknown'))

        target_language = getattr(task_config, "target_language", config.get("target_language", "Chinese"))
        if translate_during_analysis:
            system_prompt = _append_glossary_analysis_translation_instruction(system_prompt, target_language)
            _analysis_state["translate_during_analysis"] = True
            _add_analysis_log_i18n(
                "msg_glossary_analysis_translate_enabled",
                "已启用分析阶段直译：LLM 将同时输出译名和注释。"
            )

        all_terms = []
        structured_analysis = _empty_glossary_analysis_payload()

        if normalized_mode == "full":
            _analysis_state["total"] = 1
            _analysis_state["message"] = _web_tr("glossary_log_full_prepare", "全本/按比例提取：准备一次性分析 {} 行文本", lines_to_analyze, lang=lang)
            _add_analysis_log_i18n("glossary_log_full_mode_detail", "分析模式: 全本/按比例提取（推荐），将所选文本一次性发送给LLM")
            messages = [{"role": "user", "content": selected_text}]
            try:
                requester = LLMRequester()
                skip, _, response, prompt_tokens, completion_tokens = requester.sent_request(messages, system_prompt, platform_config)

                if not skip and response:
                    parsed = _parse_glossary_response(response)
                    terms = parsed.get("terms", [])
                    all_terms.extend(terms)
                    _merge_glossary_analysis_payload(structured_analysis, parsed)
                    _analysis_state["progress"] = 1
                    _analysis_state["message"] = _web_tr("glossary_log_analysis_done_tokens", "分析完成，Token: {}", f"{prompt_tokens}+{completion_tokens}", lang=lang)
                    _add_analysis_log_i18n(
                        "glossary_log_single_done",
                        "单次分析完成，发现 {} 个候选术语，Token: {}",
                        len(terms),
                        f"{prompt_tokens}+{completion_tokens}"
                    )
                else:
                    _analysis_state["progress"] = 1
                    _add_analysis_log_i18n("glossary_log_single_empty", "单次分析失败或返回为空")
            except Exception as e:
                _analysis_state["progress"] = 1
                _add_analysis_log_i18n("glossary_log_single_error", "单次分析错误: {}", str(e))
        elif normalized_mode == "split":
            batch_size = int(config.get("glossary_analysis_split_lines") or config.get("lines_limit", 20) or 20)
            batch_size = max(1, batch_size)
            batches = [items_to_analyze[i:i+batch_size] for i in range(0, len(items_to_analyze), batch_size)]

            _analysis_state["total"] = len(batches)
            _analysis_state["message"] = _web_tr("glossary_log_split_prepare", "拆分提取：准备分析 {} 行文本，共 {} 批次", lines_to_analyze, len(batches), lang=lang)
            _add_analysis_log_i18n("glossary_log_split_mode_detail", "分析模式: 拆分提取（不推荐），共 {} 批次，每批 {} 行", len(batches), batch_size)

            terms_lock = threading.Lock()
            # 只有在使用临时配置时才使用 temp_threads，否则使用当前配置的线程数
            if use_temp and temp_threads and temp_threads > 0:
                thread_count = temp_threads
            else:
                thread_count = task_config.actual_thread_counts
            _add_analysis_log_i18n("glossary_log_thread_count", "并发线程数: {}", thread_count)

            def analyze_batch(batch_info):
                batch_idx, batch = batch_info
                text_content = "\n".join([item.source_text for item in batch])
                messages = [{"role": "user", "content": text_content}]

                try:
                    requester = LLMRequester()
                    skip, _, response, _, _ = requester.sent_request(messages, system_prompt, platform_config)

                    if not skip and response:
                        parsed = _parse_glossary_response(response)
                        terms = parsed.get("terms", [])
                        with terms_lock:
                            all_terms.extend(terms)
                            _merge_glossary_analysis_payload(structured_analysis, parsed)
                            _analysis_state["progress"] += 1
                            _analysis_state["message"] = _web_tr(
                                "glossary_log_batch_progress",
                                "已完成 {}/{} 批次",
                                _analysis_state["progress"],
                                _analysis_state["total"],
                                lang=lang
                            )
                    else:
                        with terms_lock:
                            _analysis_state["progress"] += 1
                except Exception:
                    with terms_lock:
                        _analysis_state["progress"] += 1

            # Run analysis
            _analysis_state["message"] = _web_tr("msg_starting_concurrent", "开始并发分析...", lang=lang)
            _add_analysis_log_i18n("msg_starting_concurrent", "开始并发分析...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as executor:
                batch_infos = list(enumerate(batches))
                list(executor.map(analyze_batch, batch_infos))
        else:
            target_tokens = _get_incremental_split_target_tokens(config, incremental_split_target_tokens)
            batches = _split_glossary_items_by_tokens(items_to_analyze, target_tokens)
            _analysis_state["total"] = len(batches)
            _analysis_state["message"] = _web_tr("glossary_log_incremental_split_prepare", "超长增量分批：准备分析 {} 行文本，共 {} 批次", lines_to_analyze, len(batches), lang=lang)
            _add_analysis_log_i18n(
                "glossary_log_incremental_split_mode_detail",
                "分析模式: 超长增量分批，共 {} 批次，目标每批约 {} Token（单批最大 256000）；后一批参考前面累计结果，只输出新增或变化。",
                len(batches),
                target_tokens,
            )

            for batch_idx, batch in enumerate(batches):
                text_content = "\n".join([item.source_text for item in batch])
                batch_context = _build_incremental_split_context(structured_analysis, all_terms)
                batch_system_prompt = _append_incremental_split_instruction(system_prompt, batch_context, batch_idx, len(batches))
                messages = [{"role": "user", "content": text_content}]
                terms = []
                try:
                    requester = LLMRequester()
                    skip, _, response, _, _ = requester.sent_request(messages, batch_system_prompt, platform_config)
                    if not skip and response:
                        parsed = _parse_glossary_response(response)
                        terms = parsed.get("terms", [])
                        all_terms.extend(terms)
                        _merge_glossary_analysis_payload(
                            structured_analysis,
                            parsed,
                            fill_existing=True,
                            replace_existing=True,
                        )
                    _analysis_state["progress"] += 1
                    _analysis_state["message"] = _web_tr(
                        "glossary_log_batch_progress",
                        "已完成 {}/{} 批次",
                        _analysis_state["progress"],
                        _analysis_state["total"],
                        lang=lang,
                    )
                    _add_analysis_log_i18n(
                        "glossary_log_incremental_batch_done",
                        "增量分批 {}/{} 完成，新增/变化候选术语 {} 个",
                        batch_idx + 1,
                        len(batches),
                        len(terms),
                    )
                except Exception as e:
                    _analysis_state["progress"] += 1
                    _add_analysis_log_i18n("glossary_log_single_error", "单次分析错误: {}", str(e))

        if normalized_mode == "incremental_split":
            all_terms = _dedupe_glossary_terms(all_terms)
        structured_analysis = _finalize_glossary_analysis_payload(structured_analysis, all_terms)

        # Calculate frequency
        term_freq = _calculate_term_frequency(all_terms, selected_text)

        # Convert to list format
        results = []
        for term, data in term_freq.items():
            results.append({
                "src": term,
                "type": data["type"],
                "dst": data.get("dst", ""),
                "info": data.get("info", "null"),
                "count": data["count"]
            })

        _analysis_state["status"] = "completed"
        _analysis_state["message"] = _web_tr("glossary_log_analysis_completed_terms", "分析完成，发现 {} 个专有名词", len(results), lang=lang)
        _analysis_state["results"] = results
        _analysis_state["structured_analysis"] = structured_analysis
        _add_analysis_log_i18n("glossary_log_analysis_completed_terms", "分析完成，发现 {} 个专有名词", len(results))

    except Exception as e:
        _analysis_state["status"] = "error"
        _analysis_state["message"] = _web_tr("glossary_log_analysis_error_detail", "分析出错: {}", str(e))
        _add_analysis_log_i18n("glossary_log_error_message", "错误: {}", str(e))

def _empty_glossary_analysis_payload() -> dict:
    return {
        "terms": [],
        "exclusion_list_data": [],
        "characterization_data": [],
        "world_building_content": "",
        "writing_style_content": "",
        "translation_example_data": [],
    }

def _merge_glossary_analysis_payload(target: dict, source: dict, fill_existing: bool = False, replace_existing: bool = False) -> dict:
    if not source:
        return target
    target.setdefault("terms", []).extend(source.get("terms", []))
    _extend_unique_dicts(target.setdefault("exclusion_list_data", []), source.get("exclusion_list_data", []), ("markers", "regex"))
    _merge_character_lists(
        target.setdefault("characterization_data", []),
        source.get("characterization_data", []),
        fill_existing=fill_existing,
        replace_existing=replace_existing,
    )
    _extend_unique_dicts(target.setdefault("translation_example_data", []), source.get("translation_example_data", []), ("src", "dst"))
    target["world_building_content"] = _append_text_block(target.get("world_building_content", ""), source.get("world_building_content", ""))
    target["writing_style_content"] = _append_text_block(target.get("writing_style_content", ""), source.get("writing_style_content", ""))
    return target

def _finalize_glossary_analysis_payload(payload: dict, terms: list) -> dict:
    _merge_character_lists(payload.setdefault("characterization_data", []), _derive_characters_from_terms(terms))
    if not _normalize_glossary_text(payload.get("world_building_content")):
        payload["world_building_content"] = _derive_world_building_from_terms(terms)
    return payload

def _append_incremental_split_instruction(system_prompt: str, accumulated_context: dict, batch_index: int, total_batches: int) -> str:
    context_json = json.dumps(accumulated_context or {}, ensure_ascii=False, indent=2)
    instruction = f"""

## 超长文本增量分批模式
当前文本因预估 Token 过高被顺序拆分分析。你正在分析第 {batch_index + 1}/{total_batches} 批。

请遵守以下规则：
- 先阅读“已累计规则快照”，再阅读当前批文本。
- 当前批只输出新增项，或相对已累计快照有明确变化、补充、纠错价值的条目。
- 如果某个术语、角色、世界观或文风信息已经存在且当前批没有提供新证据，不要重复输出。
- 如果当前批揭示了同一术语/角色/设定的新含义、身份反转、称呼变化、语气变化、用途变化或更准确描述，可以输出更新后的条目，程序会按当前最新结果合并。
- 这是同一文件内部的分批，不是系列卷号增量；不要输出 Vol_2、Vol_3、第2卷、第3卷、volume、updated_volume、history 等卷号/时间线标识，除非原文本身明确出现这些内容且它们是需要提取的术语。
- 如果当前批没有新增或变化，对应字段返回空数组或空字符串。

### 已累计规则快照
{context_json}
"""
    return f"{system_prompt.rstrip()}\n{instruction.strip()}\n"

def _build_incremental_split_context(structured_analysis: dict, terms: list) -> dict:
    return {
        "prompt_dictionary_data": _trim_glossary_context_value(_dedupe_glossary_terms(terms), max_items=200, max_chars=12000),
        "exclusion_list_data": _trim_glossary_context_value((structured_analysis or {}).get("exclusion_list_data", []), max_items=120, max_chars=8000),
        "characterization_data": _trim_glossary_context_value((structured_analysis or {}).get("characterization_data", []), max_items=120, max_chars=12000),
        "world_building_content": _trim_glossary_context_value((structured_analysis or {}).get("world_building_content", ""), max_items=0, max_chars=10000),
        "writing_style_content": _trim_glossary_context_value((structured_analysis or {}).get("writing_style_content", ""), max_items=0, max_chars=6000),
        "translation_example_data": _trim_glossary_context_value((structured_analysis or {}).get("translation_example_data", []), max_items=80, max_chars=8000),
    }

def _trim_glossary_context_value(value, max_items: int = 300, max_chars: int = 12000):
    if isinstance(value, list):
        trimmed = value[:max_items]
        if len(value) > max_items:
            trimmed = [*trimmed, {"_truncated": f"{len(value) - max_items} more items omitted"}]
        return trimmed
    text = _normalize_glossary_text(value)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n...({len(text) - max_chars} chars omitted)"
    return text

def _dedupe_glossary_terms(terms: list) -> list:
    result = []
    by_src = {}
    for term in terms or []:
        if not isinstance(term, dict):
            continue
        src = _normalize_glossary_text(term.get("src"))
        if not src:
            continue
        if src not in by_src:
            item = dict(term)
            by_src[src] = item
            result.append(item)
            continue
        existing = by_src[src]
        for key, value in term.items():
            if key == "src":
                continue
            if _rule_field_has_content(value):
                existing[key] = value
    return result

def _parse_glossary_response(response: str) -> dict:
    import re
    payload = _empty_glossary_analysis_payload()
    parsed = _load_json_from_glossary_response(response, re)

    if isinstance(parsed, list):
        payload["terms"] = _normalize_term_items(parsed)
        return payload

    if not isinstance(parsed, dict):
        return payload

    payload["terms"] = _normalize_term_items(_first_present(parsed, ("glossary", "terms", "terminology", "term_list", "prompt_dictionary_data"), []))
    payload["exclusion_list_data"] = _normalize_exclusion_items(_first_present(parsed, ("exclusion_list", "non_translation_list", "no_translate", "ntl", "exclusion_list_data"), []))
    payload["characterization_data"] = _normalize_character_items(_first_present(parsed, ("characterization", "characters", "character_profiles", "characterization_data"), []))
    payload["world_building_content"] = _format_analysis_sections(_first_present(parsed, ("world_building", "worldview", "world_settings", "setting", "world_building_content"), ""))
    payload["writing_style_content"] = _format_analysis_sections(_first_present(parsed, ("writing_style", "style", "translation_style", "writing_style_content"), ""))
    payload["translation_example_data"] = _normalize_translation_examples(_first_present(parsed, ("translation_examples", "translation_example", "translation_example_data"), []))
    return payload

def _load_json_from_glossary_response(response: str, re_module):
    if not response:
        return None
    text = response.strip()
    candidates = []
    fence_match = re_module.search(r"```(?:json)?\s*([\s\S]*?)```", text, re_module.IGNORECASE)
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    candidates.append(text)
    object_match = re_module.search(r"\{[\s\S]*\}", text)
    if object_match:
        candidates.append(object_match.group())
    array_match = re_module.search(r"\[[\s\S]*\]", text)
    if array_match:
        candidates.append(array_match.group())
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None

def _first_present(data: dict, keys: tuple, default):
    for key in keys:
        if key in data:
            value = data.get(key)
            return default if value is None else value
    return default

def _normalize_term_items(items) -> list:
    if isinstance(items, dict):
        items = items.get("items") or items.get("data") or []
    if not isinstance(items, list):
        return []
    terms = []
    for item in items:
        if not isinstance(item, dict):
            continue
        src = _normalize_glossary_text(item.get("src") or item.get("term") or item.get("name") or item.get("original"))
        if not src:
            continue
        category = _normalize_glossary_text(item.get("category"))
        term_type = _normalize_glossary_text(item.get("type") or category, "专有名词")
        raw_info = _normalize_glossary_info(item)
        terms.append({
            "src": src,
            "dst": _normalize_glossary_text(item.get("dst") or item.get("target") or item.get("translation") or item.get("translated_name")),
            "type": term_type,
            "category": category,
            "info": _clean_glossary_analysis_info(raw_info, term_type, category),
        })
    return terms

def _normalize_exclusion_items(items) -> list:
    if isinstance(items, dict):
        items = items.get("items") or items.get("data") or []
    if isinstance(items, str):
        items = [{"markers": line.strip()} for line in items.splitlines() if line.strip()]
    if not isinstance(items, list):
        return []
    result = []
    seen = set()
    for item in items:
        if isinstance(item, str):
            item = {"markers": item}
        if not isinstance(item, dict):
            continue
        markers = _normalize_glossary_text(item.get("markers") or item.get("marker") or item.get("src") or item.get("text"))
        regex = _normalize_glossary_text(item.get("regex"))
        info = _normalize_glossary_text(item.get("info") or item.get("description") or item.get("desc"))
        key = (markers, regex)
        if (not markers and not regex) or key in seen:
            continue
        seen.add(key)
        result.append({"markers": markers, "info": info, "regex": regex})
    return result

def _normalize_character_items(items) -> list:
    import re
    if isinstance(items, dict):
        items = items.get("items") or items.get("data") or []
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        original_name = _normalize_glossary_text(item.get("original_name") or item.get("src") or item.get("name") or item.get("original"))
        if not original_name:
            continue
        additional_parts = []
        for key in ("identity", "role", "relationship", "relationships", "info", "description", "desc", "note", "annotation"):
            value = _normalize_glossary_text(item.get(key))
            if value:
                additional_parts.append(value)
        result.append({
            "original_name": original_name,
            "translated_name": _normalize_glossary_text(item.get("translated_name") or item.get("dst") or item.get("translation")),
            "aliases": _normalize_aliases(
                item.get("aliases")
                or item.get("alias")
                or item.get("nicknames")
                or item.get("other_names")
                or item.get("别名")
                or item.get("昵称")
                or item.get("称呼")
                or item.get("其他称呼"),
                re,
            ),
            "gender": _normalize_glossary_text(item.get("gender")),
            "age": _normalize_glossary_text(item.get("age")),
            "personality": _normalize_glossary_text(item.get("personality")),
            "speech_style": _normalize_glossary_text(item.get("speech_style") or item.get("speaking_style") or item.get("tone")),
            "pronouns": _normalize_glossary_text(item.get("pronouns") or item.get("first_second_person") or item.get("person_pronouns")),
            "speech_quirks": _normalize_glossary_text(item.get("speech_quirks") or item.get("verbal_quirks") or item.get("catchphrase") or item.get("ending_particles")),
            "additional_info": _normalize_glossary_text(item.get("additional_info"), "；".join(dict.fromkeys(additional_parts))),
        })
    return result

def _normalize_aliases(value, re_module) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        text = _normalize_glossary_text(value)
        if not text:
            return []
        raw_items = re_module.split(r"[,;|/，、；／\n]+", text.replace("[Separator]", "\n"))

    aliases = []
    seen = set()
    for item in raw_items:
        alias = _normalize_glossary_text(item)
        if not alias:
            continue
        marker = alias.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        aliases.append(alias)
    return aliases

def _normalize_translation_examples(items) -> list:
    if isinstance(items, dict):
        items = items.get("items") or items.get("data") or []
    if not isinstance(items, list):
        return []
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        src = _normalize_glossary_text(item.get("src") or item.get("source") or item.get("original"))
        dst = _normalize_glossary_text(item.get("dst") or item.get("target") or item.get("translation"))
        key = (src, dst)
        if not src or key in seen:
            continue
        seen.add(key)
        result.append({"src": src, "dst": dst})
    return result

def _format_analysis_sections(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n\n".join(block for block in (_format_analysis_sections(item) for item in value) if block)
    if isinstance(value, dict):
        title = _normalize_glossary_text(value.get("title") or value.get("name") or value.get("category"))
        content = value.get("content")
        if content is None:
            content = value.get("description") or value.get("info") or value.get("summary")
        content_text = _format_analysis_sections(content) if isinstance(content, (list, dict)) else _normalize_glossary_text(content)
        if not content_text:
            parts = []
            for key, item_value in value.items():
                if key in ("title", "name", "category"):
                    continue
                item_text = _format_analysis_sections(item_value)
                if item_text:
                    parts.append(f"{key}: {item_text}")
            content_text = "\n".join(parts)
        return f"## {title}\n{content_text}" if title and content_text else content_text
    return str(value).strip()

def _append_text_block(existing: str, addition: str) -> str:
    existing = _normalize_glossary_text(existing)
    addition = _normalize_glossary_text(addition)
    if not addition:
        return existing
    if addition in existing:
        return existing
    return f"{existing.rstrip()}\n\n{addition}" if existing else addition

def _extend_unique_dicts(target: list, incoming: list, key_fields: tuple) -> list:
    seen = {
        tuple(_normalize_glossary_text(item.get(field)) for field in key_fields)
        for item in target
        if isinstance(item, dict)
    }
    for item in incoming or []:
        if not isinstance(item, dict):
            continue
        key = tuple(_normalize_glossary_text(item.get(field)) for field in key_fields)
        if not any(key) or key in seen:
            continue
        target.append(item)
        seen.add(key)
    return target

def _merge_character_lists(target: list, incoming: list, fill_existing: bool = False, replace_existing: bool = False) -> list:
    by_name = {
        _normalize_glossary_text(item.get("original_name")): item
        for item in target
        if isinstance(item, dict) and _normalize_glossary_text(item.get("original_name"))
    }
    for item in incoming or []:
        if not isinstance(item, dict):
            continue
        name = _normalize_glossary_text(item.get("original_name"))
        if not name:
            continue
        existing = by_name.get(name)
        if existing:
            if fill_existing or replace_existing:
                for key, value in item.items():
                    if not _rule_field_has_content(value):
                        continue
                    if replace_existing or not _rule_field_has_content(existing.get(key)):
                        existing[key] = value
            continue
        target.append(item)
        by_name[name] = item
    return target

def _derive_characters_from_terms(terms: list) -> list:
    result = []
    for term in terms:
        term_type = _normalize_glossary_text(term.get("type")).lower()
        category = _normalize_glossary_text(term.get("category")).lower()
        if not any(key in term_type or key in category for key in ("人名", "人物", "角色", "character", "person")):
            continue
        src = _normalize_glossary_text(term.get("src"))
        if not src:
            continue
        info = _normalize_glossary_text(term.get("info"))
        result.append({
            "original_name": src,
            "translated_name": "",
            "aliases": [],
            "gender": "",
            "age": "",
            "personality": "",
            "speech_style": "",
            "pronouns": "",
            "speech_quirks": "",
            "additional_info": "" if info.lower() in ("null", "none") else info,
        })
    return result

def _derive_world_building_from_terms(terms: list) -> str:
    lines = []
    for term in terms:
        term_type = _normalize_glossary_text(term.get("type"))
        category = _normalize_glossary_text(term.get("category"))
        type_text = f"{category}/{term_type}" if category and category != term_type else term_type
        type_lower = type_text.lower()
        if any(key in type_lower for key in ("人名", "人物", "角色", "character", "person")):
            continue
        if not any(key in type_lower for key in (
            "世界", "设定", "地名", "地点", "组织", "势力", "技能", "能力", "系统",
            "术语", "place", "location", "organization", "faction", "skill", "ability",
            "system", "world", "setting", "term",
        )):
            continue
        src = _normalize_glossary_text(term.get("src"))
        if not src:
            continue
        info = _normalize_glossary_text(term.get("info"))
        suffix = "" if info.lower() in ("", "null", "none") else f"：{info}"
        lines.append(f"- {src}（{type_text or '设定'}）{suffix}")
    title = _web_tr("glossary_world_building_clues_title", "世界观与设定线索")
    return f"## {title}\n" + "\n".join(dict.fromkeys(lines)) if lines else ""

def _normalize_glossary_text(value, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default

def _build_analysis_glossary_item(row: dict) -> dict:
    src = _normalize_glossary_text((row or {}).get("src"))
    info = _clean_glossary_analysis_info(
        (row or {}).get("info"),
        (row or {}).get("type"),
        (row or {}).get("category"),
    )
    return {
        "src": src,
        "dst": _normalize_glossary_text((row or {}).get("dst")),
        "info": _normalize_glossary_text(info, "null"),
    }

def _rule_field_has_content(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_normalize_glossary_text(item) for item in value)
    return bool(_normalize_glossary_text(value))

def _normalize_glossary_info(item: dict) -> str:
    for key in ("info", "description", "desc"):
        if key in item:
            value = item.get(key)
            if value is None:
                return "null"
            text = str(value).strip()
            return text if text else "null"
    return "null"

def _append_glossary_analysis_translation_instruction(system_prompt: str, target_language: str) -> str:
    instruction = f"""

Additional output requirement:
- Translate extracted terms into "{target_language}" during analysis. Put the translation in the glossary item field "dst".
- "src" is the source-text matching key; "dst" is the replacement value that may be inserted verbatim into translated text.
- "dst" must contain only the final translated surface form. Never output "translation (note)", "translation / note", "translation: note", "translation - note", or any other "translation + explanation" form.
- Never put identity, role labels, character relationships, context notes, disambiguation, jokes, memes, or reader-facing hints in "dst"; put them in "info" or "characterization".
- Qualifiers such as left/right/front/back or numbers may appear in "dst" only when explicitly present in "src". If no pure replacement-safe translation is certain, leave "dst" empty.
- Keep "src" as the original text. Do not put category/type labels into "info".
- "type" is only the category, such as character, place, organization, skill, world setting, item, or term.
- "info" must be a short note/annotation in "{target_language}" that explains the term's role, context, or usage.
- For character profiles, fill "translated_name" and write gender, personality, speech_style, and additional_info in "{target_language}" when known.
- For world_building_content, writing_style_content, and translation_example_data.dst, output "{target_language}" content.
- For exclusion_list_data.markers, keep the original text that must not be translated; write its "info" in "{target_language}".
"""
    return f"{system_prompt.rstrip()}\n{instruction.strip()}\n"

def _clean_glossary_analysis_info(info, term_type: str = "", category: str = "") -> str:
    info = _normalize_glossary_text(info, "null")
    if info.lower() in ("", "null", "none"):
        return "null"

    labels = {
        _normalize_glossary_text(term_type).lower(),
        _normalize_glossary_text(category).lower(),
        "专有名词", "人名", "人物", "角色", "地名", "地点", "组织", "势力",
        "技能", "能力", "物品", "道具", "世界观", "设定", "术语",
        "character", "person", "place", "location", "organization", "faction",
        "skill", "ability", "item", "world", "setting", "term",
    }
    labels = {label for label in labels if label}
    for splitter in ("|", "｜", ":", "："):
        if splitter not in info:
            continue
        left, right = info.split(splitter, 1)
        if left.strip().lower() in labels:
            cleaned = right.strip()
            return cleaned if cleaned else "null"
    return info

def _format_glossary_info(term_type, info) -> str:
    term_type = _normalize_glossary_text(term_type, "专有名词")
    info = _normalize_glossary_text(info, "null")
    if info.lower() in ("null", "none"):
        return f"{term_type} | null"
    return f"{term_type} | {info}"

def _calculate_term_frequency(terms: list, source_text: str = "") -> dict:
    freq = {}
    for term in terms:
        src = term.get('src', '').strip()
        if not src:
            continue
        count = max(1, _count_glossary_term_occurrences(source_text, src) if source_text else 1)
        if src in freq:
            freq[src]['count'] = max(freq[src]['count'], count)
            if not freq[src].get('dst') and term.get('dst'):
                freq[src]['dst'] = term.get('dst')
            if freq[src].get('info') in ("", "null") and term.get('info') not in ("", None, "null"):
                freq[src]['info'] = term.get('info')
        else:
            freq[src] = {
                'count': count,
                'type': term.get('type', '专有名词'),
                'category': term.get('category', ''),
                'dst': term.get('dst', ''),
                'info': term.get('info', 'null')
            }
    return dict(sorted(freq.items(), key=lambda x: x[1]['count'], reverse=True))

def _has_non_glossary_analysis(payload: dict) -> bool:
    if not payload:
        return False
    return any([
        bool(payload.get("exclusion_list_data")),
        bool(payload.get("characterization_data")),
        bool(_normalize_glossary_text(payload.get("world_building_content"))),
        bool(_normalize_glossary_text(payload.get("writing_style_content"))),
        bool(payload.get("translation_example_data")),
    ])

def _build_analysis_rules_config(glossary_data: list, structured_analysis: dict) -> dict:
    return normalize_rules_payload({
        "prompt_dictionary_data": glossary_data,
        "exclusion_list_data": structured_analysis.get("exclusion_list_data", []),
        "characterization_data": structured_analysis.get("characterization_data", []),
        "world_building_content": _normalize_glossary_text(structured_analysis.get("world_building_content")),
        "writing_style_content": _normalize_glossary_text(structured_analysis.get("writing_style_content")),
        "translation_example_data": structured_analysis.get("translation_example_data", []),
    })

def _sanitize_rules_profile_name(profile_name: str) -> str:
    try:
        return sanitize_profile_name(profile_name, allow_none=True)
    except ValueError:
        return ""

@app.post("/api/glossary/analysis/stop")
async def stop_glossary_analysis():
    global _analysis_state
    from ModuleFolders.Base.Base import Base
    Base.work_status = Base.STATUS.STOPING
    _analysis_state["status"] = "idle"
    _analysis_state["message"] = _web_tr("msg_task_stopped", "已停止", lang=_analysis_state.get("lang"))
    return {"message": "Analysis stopped"}

class SaveAnalysisRequest(BaseModel):
    min_frequency: int = 1
    filename: str = "auto_glossary"

@app.post("/api/glossary/analysis/save")
async def save_analysis_results(request: SaveAnalysisRequest):
    """保存分析结果为新的rules_profile并自动切换"""
    global _analysis_state, _config_cache

    if _analysis_state["status"] != "completed":
        raise HTTPException(status_code=400, detail="No completed analysis to save")
    lang = _analysis_state.get("lang") or _get_web_i18n_lang()

    # Filter by frequency
    filtered = [r for r in _analysis_state["results"] if r["count"] >= request.min_frequency]
    structured_analysis = _analysis_state.get("structured_analysis") or _empty_glossary_analysis_payload()

    if not filtered and not _has_non_glossary_analysis(structured_analysis):
        raise HTTPException(status_code=400, detail="No terms or structured rules after filtering")

    # Convert to glossary format
    glossary_data = [
        _build_analysis_glossary_item(r)
        for r in filtered
    ]

    # 新建一个 rules profile 文件
    new_profile_name = _sanitize_rules_profile_name(request.filename)
    if not new_profile_name:
        raise HTTPException(status_code=400, detail=_web_tr("msg_rules_profile_name_required", "规则配置名不能为空", lang=lang))
    if new_profile_name == "None":
        raise HTTPException(status_code=400, detail=_web_tr("msg_rules_profile_reserved", "规则配置名不能使用保留名称 None", lang=lang))
    new_profile_path, new_profile_name = resolve_profile_path(
        RULES_PROFILES_PATH,
        new_profile_name,
        allow_none=True,
    )

    # 确保目录存在
    os.makedirs(RULES_PROFILES_PATH, exist_ok=True)
    if os.path.exists(new_profile_path):
        raise HTTPException(status_code=400, detail=_web_tr("msg_rules_profile_exists", "规则配置已存在: {}", new_profile_name, lang=lang))

    # 创建新的 rules profile，包含本次分析出的全部分类内容
    new_rules_config = _build_analysis_rules_config(glossary_data, structured_analysis)

    atomic_write_json(new_profile_path, new_rules_config)

    # 自动切换到新创建的 rules profile
    root_config = load_root_config()
    root_config["active_rules_profile"] = new_profile_name
    save_root_config(root_config)

    # 清除缓存以便前端获取最新数据
    _config_cache.clear()

    return {
        "message": _web_tr(
            "msg_analysis_saved_to_new_rules_profile",
            "已保存到新规则配置 '{}' 并自动切换：术语 {}，禁翻 {}，角色 {}",
            new_profile_name,
            len(glossary_data),
            len(new_rules_config.get('exclusion_list_data', [])),
            len(new_rules_config.get('characterization_data', [])),
            lang=lang,
        ),
        "file": new_profile_path,
        "profile": new_profile_name,
        "count": len(glossary_data),
        "rules": new_rules_config
    }

# --- Plugin Management Endpoints ---

@app.get("/api/plugins")
async def get_plugins():
    """
    Returns a list of all loaded plugins and their enable status.
    """
    try:
        # We need an instance of PluginManager to get the loaded plugins
        from ModuleFolders.Base.PluginManager import PluginManager
        pm = PluginManager()
        pm.load_plugins_from_directory(os.path.join(PROJECT_ROOT, "PluginScripts"))
        
        plugins = pm.get_plugins()
        
        # Load enable status from root config
        root_config = load_root_config()
        plugin_enables = root_config.get("plugin_enables", {})
        
        result = []
        for name, plugin in plugins.items():
            result.append({
                "name": name,
                "description": plugin.description,
                "enabled": plugin_enables.get(name, plugin.default_enable),
                "default_enable": plugin.default_enable
            })
            
        return sorted(result, key=lambda x: x["name"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get plugins: {e}")

@app.post("/api/plugins/toggle")
async def toggle_plugin(request: PluginEnableRequest):
    """
    Toggles a plugin's enable status and saves it to root config.
    """
    try:
        root_config = load_root_config()
        plugin_enables = root_config.get("plugin_enables", {})
        plugin_enables[request.name] = request.enabled
        root_config["plugin_enables"] = plugin_enables
        save_root_config(root_config)
            
        return {"message": f"Plugin '{request.name}' {'enabled' if request.enabled else 'disabled'}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to toggle plugin: {e}")

@app.get("/api/profiles", response_model=List[str])
async def get_profiles():
    """
    Returns a list of available profile filenames, utilizing cache.
    """
    global _profiles_cache

    if _profiles_cache is not None:
        return _profiles_cache

    _profiles_cache = list_profile_names(PROFILES_PATH)
    return _profiles_cache

@app.get("/api/rules_profiles", response_model=List[str])
async def get_rules_profiles():
    return list_profile_names(RULES_PROFILES_PATH, include_none=True)

# --- Prompt Management Endpoints ---

@app.get("/api/prompts")
async def list_prompt_categories():
    base_dir = os.path.join(PROJECT_ROOT, "Resource", "Prompt")
    if not os.path.exists(base_dir): return []
    return sorted([d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))])

@app.get("/api/prompts/{category}")
async def list_prompts(category: str):
    # category: "Translate", "Polishing", "Local", "Sakura", "System"
    prompt_dir = os.path.join(PROJECT_ROOT, "Resource", "Prompt", category)
    if not os.path.exists(prompt_dir):
        return []
    # Support both .txt and .json (for error_analysis.json)
    files = [f for f in os.listdir(prompt_dir) if f.endswith((".txt", ".json"))]
    return sorted(files)

@app.get("/api/prompts/{category}/{filename}")
async def get_prompt_content(category: str, filename: str):
    # Try literal match first, then fallback to .txt
    file_path = os.path.join(PROJECT_ROOT, "Resource", "Prompt", category, filename)
    if not os.path.exists(file_path):
        if not filename.endswith(".txt"):
            file_path += ".txt"
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Prompt file not found")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return {"content": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read prompt: {e}")

@app.post("/api/prompts/{category}/{filename}")
async def save_prompt_content(category: str, filename: str, data: Dict[str, str] = Body(...)):
    file_path = os.path.join(PROJECT_ROOT, "Resource", "Prompt", category, filename)
    # Check if we should append .txt (only if it doesn't exist and doesn't have an extension)
    if not os.path.exists(file_path) and "." not in filename:
        file_path += ".txt"
        
    content = data.get("content", "")
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return {"message": "Prompt saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save prompt: {e}")

@app.post("/api/rules_profiles/switch")
async def switch_rules_profile(request: RulesProfileSwitchRequest, http_request: Request):
    global _config_cache
    try:
        profile_name = sanitize_profile_name(request.profile, allow_none=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if profile_name != "None":
        profile_path, profile_name = resolve_profile_path(RULES_PROFILES_PATH, profile_name, allow_none=True)
        if not os.path.exists(profile_path):
            raise HTTPException(status_code=404, detail="Rules profile not found")

    try:
        root_config = load_root_config()
        root_config["active_rules_profile"] = profile_name
        save_root_config(root_config)

        _config_cache.clear()
        return await get_config(http_request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/rules_profiles/delete")
async def delete_rules_profile(request: RulesProfileDeleteRequest):
    global _config_cache
    try:
        profile_name = sanitize_profile_name(request.profile, allow_none=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if profile_name == "None":
        raise HTTPException(status_code=400, detail="Cannot delete the None rules profile")

    root_config = load_root_config()
    if root_config.get("active_rules_profile", "default") == profile_name:
        raise HTTPException(status_code=400, detail="Cannot delete the active rules profile")

    profile_path, profile_name = resolve_profile_path(RULES_PROFILES_PATH, profile_name, allow_none=True)
    if not os.path.exists(profile_path):
        raise HTTPException(status_code=404, detail="Rules profile not found")

    try:
        os.remove(profile_path)
        _config_cache.clear()
        return {"message": f"Rules profile '{profile_name}' deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete rules profile: {e}")

@app.post("/api/profiles/switch")
async def switch_profile(request: ProfileSwitchRequest, http_request: Request):
    """
    Switches the active profile, returns the new active config, and invalidates caches.
    """
    global _config_cache, _profiles_cache # Need to clear these caches

    try:
        profile_path, profile_name = resolve_profile_path(PROFILES_PATH, request.profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not os.path.exists(profile_path):
        raise HTTPException(status_code=404, detail=f"Profile '{profile_name}' not found.")

    try:
        # Update the root config to point to the new profile
        root_config = load_root_config()
        root_config["active_profile"] = profile_name
        save_root_config(root_config)

        # Invalidate all config caches and profiles cache
        _config_cache.clear()
        _profiles_cache = None

        return await get_config(http_request)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to switch profile: {e}")

@app.post("/api/profiles/create")
async def create_profile(request: ProfileCreateRequest):
    global _profiles_cache

    try:
        new_path, new_name = resolve_profile_path(PROFILES_PATH, request.name)
        base_name = sanitize_profile_name(request.base) if request.base else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Use injected handler if available
    if profile_handlers["create"]:
        try:
            profile_handlers["create"](new_name, base_name)
            _profiles_cache = None
            return {"message": f"Profile '{new_name}' created successfully (via host)"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    if os.path.exists(new_path):
        raise HTTPException(status_code=409, detail="Profile already exists")

    # Determine base profile to copy from
    if not base_name:
        # Use active profile as base
        _, config = get_config_mode()
        base_name = config.get("active_profile", "default")

    base_path, _ = resolve_profile_path(PROFILES_PATH, base_name)

    try:
        # Robust Creation Logic: Preset + Base -> New
        final_config = load_master_preset()
        if os.path.exists(base_path):
            final_config = deep_merge(final_config, load_json_file(base_path, {}))
        settings_only, _, _ = split_effective_config(final_config)
        atomic_write_json(new_path, settings_only)

        _profiles_cache = None # Invalidate cache
        return {"message": f"Profile '{new_name}' created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create profile: {e}")

@app.post("/api/profiles/rename")
async def rename_profile(request: ProfileRenameRequest):
    global _profiles_cache, _config_cache
    try:
        old_path, old_name = resolve_profile_path(PROFILES_PATH, request.old_name)
        new_path, new_name = resolve_profile_path(PROFILES_PATH, request.new_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Use injected handler
    if profile_handlers["rename"]:
        try:
            profile_handlers["rename"](old_name, new_name)
            _profiles_cache = None
            _config_cache.clear()
            return {"message": f"Renamed '{old_name}' to '{new_name}' (via host)"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="Source profile not found")
    if os.path.exists(new_path):
        raise HTTPException(status_code=409, detail="Destination profile name already exists")
        
    try:
        os.rename(old_path, new_path)
        
        # Check if we renamed the active profile
        _, config = get_config_mode()
        current_active = config.get("active_profile")

        if current_active == old_name:
            # Update root config to point to new name
            config["active_profile"] = new_name
            save_root_config(config)

            # Clear config cache as the key changed
            _config_cache.clear()

        _profiles_cache = None
        return {"message": f"Renamed '{old_name}' to '{new_name}'"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rename profile: {e}")

@app.post("/api/profiles/delete")
async def delete_profile(request: ProfileDeleteRequest):
    global _profiles_cache
    try:
        target_path, profile_name = resolve_profile_path(PROFILES_PATH, request.profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Use injected handler
    if profile_handlers["delete"]:
        try:
            profile_handlers["delete"](profile_name)
            _profiles_cache = None
            return {"message": f"Profile '{profile_name}' deleted (via host)"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Profile not found")

    # Check if active
    _, config = get_config_mode()
    if config.get("active_profile") == profile_name:
        raise HTTPException(status_code=400, detail="Cannot delete the currently active profile. Please switch to another profile first.")

    # Check if it's the last one (optional safety, though frontend should handle)
    profiles = [f for f in os.listdir(PROFILES_PATH) if f.endswith(".json")]
    if len(profiles) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only remaining profile.")

    try:
        os.remove(target_path)
        _profiles_cache = None
        return {"message": f"Profile '{profile_name}' deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete profile: {e}")

# --- Task API Endpoints ---

class PlatformCreateRequest(BaseModel):
    name: str
    base_config: Optional[Dict[str, Any]] = None

@app.post("/api/platforms/create")
async def create_platform(request: PlatformCreateRequest):
    global _config_cache
    new_name = request.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Platform name cannot be empty")

    try:
        config = dict(_load_active_config_payload())

        if "platforms" not in config: config["platforms"] = {}
        if new_name in config["platforms"]:
            raise HTTPException(status_code=409, detail="Platform already exists")
        
        # Use template from custom or a default
        template = config["platforms"].get("custom", {
            "tag": "custom", "group": "custom", "name": "Custom API",
            "api_url": "", "api_key": "", "api_format": "OpenAI",
            "model": "gpt-4o", "key_in_settings": ["api_url", "api_key", "model"]
        }).copy()
        
        template["tag"] = new_name
        template["name"] = new_name
        
        if request.base_config:
            template.update(request.base_config)

        config["platforms"][new_name] = template
        save_effective_config(config)

        _config_cache.clear()
        return {"message": f"Platform '{new_name}' created", "config": template}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/task/run")
async def run_task(payload: TaskPayload):
    if task_manager.status in {"running", "stopping"}:
        detail = (
            "Task process is still stopping; retry after it exits."
            if task_manager.status == "stopping"
            else "A task is already running."
        )
        raise HTTPException(status_code=409, detail=detail)

    spec = payload.to_task_spec()
    active_config = _load_active_config_payload()
    missing_prompts = _missing_prompt_selections(spec.task_type, active_config)
    if missing_prompts:
        labels = {
            "translation": _web_tr(
                "msg_prompt_guard_translation_missing",
                "Translation prompt is not selected",
                config=active_config,
            ),
            "polishing": _web_tr(
                "msg_prompt_guard_polishing_missing",
                "Polishing prompt is not selected",
                config=active_config,
            ),
        }
        detail = "; ".join(labels[item] for item in missing_prompts)
        raise HTTPException(status_code=422, detail=detail)

    if spec.manga:
        manga_status = get_manga_feature_status(require_models=False)
        if not manga_status.available:
            raise HTTPException(status_code=503, detail=manga_status.user_message())
    
    # 强制同步 Web 端缓存到磁盘，确保子进程能读取到编辑器中最新的修改
    try:
        cm = get_cache_manager()
        if hasattr(cm, 'project') and cm.project and getattr(cm, 'save_to_file_require_flag', False):
            # 获取输出路径（优先使用 payload 里的，如果没有则从当前配置读）
            output_path = spec.output_path or _load_active_config_payload().get("label_output_path")
            if output_path:
                cm.save_to_file_require_path = output_path
                cm.save_to_file()
                cm.save_to_file_require_flag = False
    except Exception as e:
        print(f"Warning: Failed to flush web cache before task start: {e}")

    if not task_manager.start_task(spec):
        raise HTTPException(status_code=500, detail="Failed to start task process.")
    
    return {"success": True, "message": "Task started successfully."}

@app.post("/api/task/stop")
async def stop_task():
    if not task_manager.stop_task():
        raise HTTPException(
            status_code=409,
            detail="Task process is still stopping; retry after it exits.",
        )
    return {"message": "Stop signal sent."}

@app.get("/api/task/status")
async def get_task_status(
    response: Response,
    log_cursor: int = Query(0, ge=0),
    chart_cursor: int = Query(0, ge=0),
    comparison_cursor: int = Query(0, ge=0)
):
    response.headers["Cache-Control"] = "no-store"
    return task_manager.snapshot_status(log_cursor, chart_cursor, comparison_cursor)

class InternalComparisonPayload(BaseModel):
    source: str
    translation: str

@app.post("/api/internal/update_comparison")
async def internal_update_comparison(payload: InternalComparisonPayload):
    """Internal endpoint for subprocesses to push comparison data."""
    task_manager.push_comparison(payload.source, payload.translation)
    return {"status": "ok"}

# --- File Management Endpoints ---

@app.post("/api/files/upload")
async def upload_file(file: UploadFile = File(...), policy: str = "default"):
    """
    Uploads a file to the project's 'updatetemp' directory with limit enforcement.
    policy: 'default' | 'buffer' | 'overwrite'
    """
    try:
        os.makedirs(UPDATETEMP_PATH, exist_ok=True)
        
        # 1. Get current sorted files
        files = []
        for f in os.listdir(UPDATETEMP_PATH):
            fp = os.path.join(UPDATETEMP_PATH, f)
            if os.path.isfile(fp):
                files.append((fp, os.path.getmtime(fp)))
        files.sort(key=lambda x: x[1]) # Oldest first
        
        # 2. Get Limit
        config = _load_active_config_payload()
        limit = config.get("temp_file_limit", 10)
        count = len(files)
        
        # 3. Logic
        if count < limit:
            pass # Safe to upload
        
        elif count == limit:
            if policy == "default":
                return {
                    "status": "limit_reached", 
                    "limit": limit,
                    "oldest": os.path.basename(files[0][0])
                }
            elif policy == "overwrite":
                try: os.remove(files[0][0])
                except: pass
            elif policy == "buffer":
                pass # Allow +1
        
        elif count >= limit + 1:
            # Force delete oldest to bring back to limit (or limit+1 if we allow swap?)
            # Requirement: "Only to the 12th file... prompt user 'Earliest has been deleted'"
            # If current is 11 (limit+1), adding 12th means we MUST delete 1st.
            # So we delete oldest, and return a warning flag.
            try: os.remove(files[0][0])
            except: pass
            
            # Now count is back to limit (10). Wait, if we had 11, deleting 1 makes 10.
            # Then we save new file -> 11.
            # So we are effectively rotating at limit+1.
            return {
                "status": "forced_delete",
                "limit": limit,
                "deleted": os.path.basename(files[0][0]),
                "path": "" # Will be filled after save
            }

        # 4. Save File
        file_location = os.path.join(UPDATETEMP_PATH, file.filename)
        # Security check
        if not os.path.abspath(file_location).startswith(os.path.abspath(UPDATETEMP_PATH)):
             raise HTTPException(status_code=400, detail="Invalid file path")

        with open(file_location, "wb+") as file_object:
            file_object.write(await file.read())
            
        return {"info": f"file '{file.filename}' saved", "path": file_location}

    except Exception as e:
        # If it was our custom return, don't wrap it in 500
        if isinstance(e, HTTPException): raise e
        # If the return was a dict (status logic above), fastapi handles it? 
        # No, async def returns JSON directly. 
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}")

@app.get("/api/files/temp")
async def list_temp_files():
    """
    Lists files in the 'updatetemp' directory.
    """
    if not os.path.exists(UPDATETEMP_PATH):
        return []
    
    files = []
    for f in os.listdir(UPDATETEMP_PATH):
        full_path = os.path.join(UPDATETEMP_PATH, f)
        if os.path.isfile(full_path):
            files.append({
                "name": f,
                "path": full_path,
                "size": os.path.getsize(full_path)
            })
    return files

@app.delete("/api/files/temp")
async def delete_temp_files(request: DeleteFileRequest):
    """
    Deletes specified files from the 'updatetemp' directory.
    """
    if not os.path.exists(UPDATETEMP_PATH):
        return {"deleted": [], "failed": []}
    
    deleted = []
    failed = []
    
    for filename in request.files:
        # Security: Prevent path traversal
        safe_path = os.path.join(UPDATETEMP_PATH, os.path.basename(filename))
        if os.path.exists(safe_path):
            try:
                os.remove(safe_path)
                deleted.append(filename)
            except Exception as e:
                failed.append({"file": filename, "error": str(e)})
        else:
            failed.append({"file": filename, "error": "File not found"})
            
    return {"deleted": deleted, "failed": failed}

# --- Draft Management Endpoints ---

def save_draft_generic(filename: str, data: Any):
    try:
        os.makedirs(TEMP_EDIT_PATH, exist_ok=True)
        draft_path = os.path.join(TEMP_EDIT_PATH, filename)
        with open(draft_path, 'w', encoding='utf-8') as f:
            # If data is list of models, convert to list of dicts
            if isinstance(data, list) and len(data) > 0 and hasattr(data[0], 'dict'):
                json.dump([item.dict() for item in data], f, indent=4, ensure_ascii=False)
            # If data is simple dict/list/str
            elif hasattr(data, 'dict'):
                json.dump(data.dict(), f, indent=4, ensure_ascii=False)
            else:
                json.dump(data, f, indent=4, ensure_ascii=False)
        return {"message": "Draft saved."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save draft: {e}")

def get_draft_generic(filename: str):
    draft_path = os.path.join(TEMP_EDIT_PATH, filename)
    if not os.path.exists(draft_path):
        return None # Return None to indicate no draft
    try:
        with open(draft_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return None

@app.post("/api/draft/glossary")
async def save_glossary_draft(items: List[Dict[str, Any]]):
    return save_draft_generic("glossary_draft.json", items)

@app.get("/api/draft/glossary")
async def get_glossary_draft():
    return get_draft_generic("glossary_draft.json") or []

@app.post("/api/draft/exclusion")
async def save_exclusion_draft(items: List[Dict[str, Any]]):
    return save_draft_generic("exclusion_draft.json", items)

@app.get("/api/draft/exclusion")
async def get_exclusion_draft():
    return get_draft_generic("exclusion_draft.json") or []

@app.post("/api/draft/pre_translation")
async def save_pre_translation_draft(items: List[Dict[str, Any]]):
    return save_draft_generic("pre_translation_draft.json", items)

@app.get("/api/draft/pre_translation")
async def get_pre_translation_draft():
    return get_draft_generic("pre_translation_draft.json") or []

@app.post("/api/draft/post_translation")
async def save_post_translation_draft(items: List[Dict[str, Any]]):
    return save_draft_generic("post_translation_draft.json", items)

@app.get("/api/draft/post_translation")
async def get_post_translation_draft():
    return get_draft_generic("post_translation_draft.json") or []

@app.post("/api/draft/characterization")
async def save_characterization_draft(items: List[Dict[str, Any]]):
    return save_draft_generic("characterization_draft.json", items)

@app.get("/api/draft/characterization")
async def get_characterization_draft():
    return get_draft_generic("characterization_draft.json") or []

@app.post("/api/draft/translation_example")
async def save_translation_example_draft(items: List[Dict[str, Any]]):
    return save_draft_generic("translation_example_draft.json", items)

@app.get("/api/draft/translation_example")
async def get_translation_example_draft():
    return get_draft_generic("translation_example_draft.json") or []

@app.post("/api/draft/world_building")
async def save_world_building_draft(data: StringContent):
    return save_draft_generic("world_building_draft.json", data.content)

@app.get("/api/draft/world_building")
async def get_world_building_draft():
    res = get_draft_generic("world_building_draft.json")
    if res is None: return {"content": ""}
    return {"content": res}

@app.post("/api/draft/writing_style")
async def save_writing_style_draft(data: StringContent):
    return save_draft_generic("writing_style_draft.json", data.content)

@app.get("/api/draft/writing_style")
async def get_writing_style_draft():
    res = get_draft_generic("writing_style_draft.json")
    if res is None: return {"content": ""}
    return {"content": res}

# --- Cache Management API ---

class CacheItem(BaseModel):
    id: int
    file_path: str
    text_index: int
    source: str
    translation: str
    original_translation: str
    translation_status: int
    modified: bool = False

class CacheUpdateRequest(BaseModel):
    item_id: int
    translation: str

class CacheLoadRequest(BaseModel):
    project_path: str

class CacheRestoreRequest(BaseModel):
    project_path: str
    backup_file: str

class ProofreadStartRequest(BaseModel):
    project_path: str
    report_mode: Optional[str] = None
    suggestion_mode: Optional[str] = None
    overwrite_confirmed: bool = False

class ProofreadSuggestionActionRequest(BaseModel):
    project_path: str
    report_file: Optional[str] = None
    allow_manual_edit_override: bool = False

class ProofreadReviewStateRequest(BaseModel):
    project_path: str
    report_file: Optional[str] = None
    current_suggestion_id: Optional[str] = None
    active_filter: Optional[str] = None

# Global cache manager instance
_cache_manager_instance = None

def get_cache_manager():
    """Get CacheManager singleton instance"""
    global _cache_manager_instance
    try:
        if _cache_manager_instance is None:
            from ModuleFolders.Infrastructure.Cache.CacheManager import CacheManager
            _cache_manager_instance = CacheManager()
        return _cache_manager_instance
    except ImportError:
        raise HTTPException(status_code=500, detail="CacheManager not available")

def resolve_cache_output_path(project_path: str) -> str:
    input_path = os.path.normpath(project_path.strip())
    if not input_path:
        raise HTTPException(status_code=400, detail="Project path is required")
    if input_path.endswith("AinieeCacheData.json"):
        return os.path.dirname(os.path.dirname(input_path))
    if input_path.endswith("cache"):
        return os.path.dirname(input_path)
    if "AinieeCacheData.json" in input_path:
        cache_filename_pos = input_path.find("AinieeCacheData.json")
        if cache_filename_pos != -1:
            cache_dir = input_path[:cache_filename_pos].rstrip(os.path.sep)
            return os.path.dirname(cache_dir)
    return input_path

@app.get("/api/cache/status")
async def get_cache_status():
    """Get cache loading status and basic info"""
    try:
        cache_manager = get_cache_manager()
        has_project = hasattr(cache_manager, 'project') and cache_manager.project and cache_manager.project.files

        if has_project:
            file_count = len(cache_manager.project.files)
            total_items = cache_manager.get_item_count()
            return {
                "loaded": True,
                "file_count": file_count,
                "total_items": total_items,
                "project_name": getattr(cache_manager.project, 'project_name', 'Unknown Project')
            }
        else:
            return {
                "loaded": False,
                "file_count": 0,
                "total_items": 0,
                "project_name": None
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get cache status: {e}")

@app.post("/api/cache/load")
async def load_cache(request: CacheLoadRequest):
    """Load cache data from project path"""
    try:
        cache_manager = get_cache_manager()

        input_path = request.project_path.strip()
        input_path = os.path.normpath(input_path)
        output_path = resolve_cache_output_path(input_path)

        # Validate that cache file exists before attempting to load
        if input_path.endswith("AinieeCacheData.json"):
            # User provided path to cache file directly - use it
            cache_file_to_check = input_path
        elif "AinieeCacheData.json" in input_path:
            # Path contains cache filename somewhere - extract it properly
            cache_filename_pos = input_path.find("AinieeCacheData.json")
            cache_file_to_check = input_path[:cache_filename_pos + len("AinieeCacheData.json")]
        else:
            # User provided project directory - construct cache file path
            cache_file_to_check = os.path.join(output_path, "cache", "AinieeCacheData.json")

        cache_file_to_check = os.path.normpath(cache_file_to_check)

        if not os.path.exists(cache_file_to_check):
            # Try to provide more helpful error information
            cache_dir = os.path.dirname(cache_file_to_check)
            if not os.path.exists(cache_dir):
                raise HTTPException(
                    status_code=404,
                    detail=f"Cache directory not found: {cache_dir}. Please check if the project path is correct."
                )
            else:
                # List files in cache directory to help debug
                try:
                    files_in_cache = os.listdir(cache_dir)
                    raise HTTPException(
                        status_code=404,
                        detail=f"Cache file 'AinieeCacheData.json' not found in {cache_dir}. Found files: {files_in_cache}"
                    )
                except PermissionError:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Cache file not found: {cache_file_to_check}. Permission denied accessing cache directory."
                    )

        # Load the main cache directly. Web users choose backups in the UI; the load
        # endpoint must not auto-heal because that would overwrite the user's choice.
        try:
            project = cache_manager.read_from_file(cache_file_to_check)
        except Exception as cache_error:
            candidates = cache_manager._collect_valid_backup_caches(output_path)
            if candidates:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Cache file is corrupted. Select a backup to restore.",
                        "backups": [
                            {
                                "file": os.path.basename(candidate["path"]),
                                "modified_time": candidate["modified_time"],
                                "size": candidate["size"],
                                "size_label": candidate["size_label"],
                                "project_name": candidate["project_name"],
                                "item_count": candidate["item_count"],
                                "translated_count": candidate["translated_count"],
                                "total_count": candidate["total_count"],
                                "completion_rate": candidate["completion_rate"],
                                "completion_label": candidate["completion_label"],
                            }
                            for candidate in candidates
                        ],
                    },
                ) from cache_error
            raise
        else:
            cache_manager.load_from_project(project)

        if not hasattr(cache_manager, 'project') or not cache_manager.project.files:
            raise HTTPException(status_code=500, detail="Failed to load cache data")

        file_count = len(cache_manager.project.files)
        total_items = cache_manager.get_item_count()

        return {
            "success": True,
            "message": f"Cache loaded successfully. Found {file_count} files with {total_items} items.",
            "file_count": file_count,
            "total_items": total_items
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load cache: {e}")

@app.get("/api/cache/backups")
async def list_cache_backups(project_path: str):
    try:
        output_path = resolve_cache_output_path(project_path)
        cache_manager = get_cache_manager()
        candidates = cache_manager._collect_valid_backup_caches(output_path)
        return {
            "backups": [
                {
                    "file": os.path.basename(candidate["path"]),
                    "modified_time": candidate["modified_time"],
                    "size": candidate["size"],
                    "size_label": candidate["size_label"],
                    "project_name": candidate["project_name"],
                    "item_count": candidate["item_count"],
                    "translated_count": candidate["translated_count"],
                    "total_count": candidate["total_count"],
                    "completion_rate": candidate["completion_rate"],
                    "completion_label": candidate["completion_label"],
                }
                for candidate in candidates
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list cache backups: {e}")

@app.post("/api/cache/restore")
async def restore_cache_backup(request: CacheRestoreRequest):
    try:
        output_path = resolve_cache_output_path(request.project_path)
        backup_file = os.path.basename(request.backup_file)
        backup_dir = os.path.join(output_path, "cache", "backups")
        backup_path = os.path.realpath(os.path.join(backup_dir, backup_file))
        backup_dir_real = os.path.realpath(backup_dir)
        if os.path.commonpath([backup_dir_real, backup_path]) != backup_dir_real:
            raise HTTPException(status_code=400, detail="Invalid backup file")
        if not os.path.isfile(backup_path):
            raise HTTPException(status_code=404, detail="Backup file not found")

        cache_manager = get_cache_manager()
        project = cache_manager.read_from_file(backup_path)
        main_cache_path = os.path.join(output_path, "cache", "AinieeCacheData.json")
        os.makedirs(os.path.dirname(main_cache_path), exist_ok=True)
        shutil.copy2(backup_path, main_cache_path)
        cache_manager.load_from_project(project)

        return {
            "success": True,
            "message": "Cache backup restored successfully",
            "file": backup_file,
            "file_count": len(cache_manager.project.files),
            "total_items": cache_manager.get_item_count(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to restore cache backup: {e}")

@app.get("/api/cache/items")
async def get_cache_items(page: int = 1, page_size: Optional[int] = None, search: str = None):
    """Get paginated cache items"""
    try:
        cache_manager = get_cache_manager()

        if not hasattr(cache_manager, 'project') or not cache_manager.project.files:
            raise HTTPException(status_code=400, detail="No cache data loaded")

        # Get page size from config if not provided
        if page_size is None:
            try:
                config = get_config_data()
                page_size = config.get('cache_editor_page_size', 15)
            except:
                page_size = 15

        # Extract items (similar to TUI's _extract_cache_items)
        items = []
        with cache_manager.file_lock:
            for file_path, cache_file in cache_manager.project.files.items():
                for idx, item in enumerate(cache_file.items):
                    if item.source_text and item.source_text.strip():
                        translation = ""
                        if item.polished_text:
                            translation = item.polished_text
                        elif item.translated_text:
                            translation = item.translated_text

                        # Include all items with source text (translated or not)
                        items.append({
                            'id': len(items),
                            'file_path': file_path,
                            'text_index': item.text_index,
                            'source': item.source_text,
                            'translation': translation,
                            'original_translation': translation,
                            'translation_status': item.translation_status,
                            'modified': item.translation_status == TranslationStatus.USER_PROOFREAD
                        })

        # Apply search filter
        if search and search.strip():
            search_lower = search.lower()
            items = [
                item for item in items
                if search_lower in item['source'].lower() or search_lower in item['translation'].lower()
            ]

        # Apply pagination
        total_items = len(items)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_items = items[start_idx:end_idx]

        total_pages = (total_items + page_size - 1) // page_size

        return {
            "items": paginated_items,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_items": total_items,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get cache items: {e}")

class CacheUpdateRequestWithPath(BaseModel):
    item_id: int
    translation: str
    project_path: str
    report_file: Optional[str] = None

@app.put("/api/cache/items/{item_id}")
async def update_cache_item(item_id: int, request: CacheUpdateRequestWithPath):
    """Update a cache item's translation"""
    try:
        cache_manager = get_cache_manager()

        if not hasattr(cache_manager, 'project') or not cache_manager.project.files:
            raise HTTPException(status_code=400, detail="No cache data loaded")

        # Parse project path same way as load_cache
        input_path = request.project_path.strip()
        input_path = os.path.normpath(input_path)

        # Determine the correct output_path for CacheManager (same logic as load_cache)
        if input_path.endswith("AinieeCacheData.json"):
            output_path = os.path.dirname(os.path.dirname(input_path))
        elif input_path.endswith("cache"):
            output_path = os.path.dirname(input_path)
        elif "AinieeCacheData.json" in input_path:
            # Handle case where path contains the filename but endswith failed
            cache_filename_pos = input_path.find("AinieeCacheData.json")
            if cache_filename_pos != -1:
                cache_dir = input_path[:cache_filename_pos].rstrip(os.path.sep)
                output_path = os.path.dirname(cache_dir)
        else:
            output_path = input_path

        _, proofread_store = _load_proofread_store(output_path, request.report_file)

        # Find the item to update
        item_found = False
        updated_file_path = ""
        updated_text_index = 0
        current_idx = 0

        with cache_manager.file_lock:
            for file_path, cache_file in cache_manager.project.files.items():
                for item in cache_file.items:
                    if item.source_text and item.source_text.strip():
                        if current_idx == item_id:
                            # Update the translation
                            new_translation = request.translation

                            if item.polished_text:
                                item.polished_text = new_translation
                            else:
                                item.translated_text = new_translation
                            item.translation_status = TranslationStatus.USER_PROOFREAD
                            if item.extra is None:
                                item.extra = {}
                            accepted_meta = item.extra.get("proofread_suggestion")
                            if isinstance(accepted_meta, dict):
                                accepted_meta["superseded_by_manual_edit"] = True
                            item.extra["proofread_manual_edit"] = {
                                "client": "web",
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            }

                            # Save to file
                            cache_manager.require_save_to_file(output_path)
                            updated_file_path = file_path
                            updated_text_index = int(item.text_index)
                            item_found = True
                            break

                        current_idx += 1

                if item_found:
                    break

        if not item_found:
            raise HTTPException(status_code=404, detail="Cache item not found")

        cache_manager.flush_pending_save()
        proofread_store.complete_manually_edited_lines(
            cache_manager.project,
            file_path=updated_file_path,
            text_index=updated_text_index,
            client="web",
        )
        has_report = bool(
            proofread_store.path.exists()
            or proofread_store.report_path.exists()
            or proofread_store.has_report_content()
        )
        return {
            "success": True,
            "message": "Cache item updated successfully",
            "report": _proofread_report_payload(proofread_store) if has_report else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update cache item: {e}")

@app.post("/api/cache/search")
async def search_cache_items(query: str, scope: str = "all", is_regex: bool = False):
    """Search cache items with advanced options"""
    try:
        cache_manager = get_cache_manager()

        if not hasattr(cache_manager, 'project') or not cache_manager.project.files:
            raise HTTPException(status_code=400, detail="No cache data loaded")

        # Use cache manager's search functionality
        results = cache_manager.search_items(query, scope, is_regex, False)

        # Convert results to web format
        search_results = []
        for file_path, line_num, cache_item in results:
            translation = cache_item.polished_text or cache_item.translated_text or ""
            search_results.append({
                "file_path": file_path,
                "line_number": line_num,
                "source": cache_item.source_text,
                "translation": translation,
                "text_index": cache_item.text_index,
                "translation_status": cache_item.translation_status
            })

        return {
            "results": search_results,
            "total_found": len(search_results),
            "query": query,
            "scope": scope
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to search cache: {e}")

# --- AI Proofread API ---

# Global state for proofread task
_proofread_state = {
    "running": False,
    "progress": 0,
    "total": 0,
    "issues": [],
    "tokens_used": 0,
    "error": None,
    "completed": False,
    "output_path": None,
}

def _load_proofread_store(project_path: str, report_file: str | None = None):
    from ModuleFolders.Service.Proofreader import ProofreadSuggestionStore

    output_path = resolve_cache_output_path(project_path)
    store = ProofreadSuggestionStore(output_path)
    if report_file:
        archive_name = str(report_file).strip()
        if (
            not archive_name
            or archive_name != os.path.basename(archive_name)
            or "/" in archive_name
            or "\\" in archive_name
        ):
            raise HTTPException(status_code=400, detail="Invalid proofread report file")
        try:
            store.load_archive(store.archive_dir / archive_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid proofread report file") from exc
    else:
        store.load()
    return output_path, store

def _proofread_report_payload(store) -> Dict[str, Any]:
    return {
        "report_file": store.storage_path.name,
        "is_archive": store.is_archive,
        "run": store.run,
        "review_state": store.review_state,
        "summary": store._build_summary(),
        "suggestions": [item.to_dict() for item in store.suggestions],
    }

def _save_loaded_cache(output_path: str) -> None:
    cache_manager = get_cache_manager()
    cache_manager.save_to_file_require_path = output_path
    cache_manager.save_to_file()

@app.get("/api/proofread/reports")
async def list_proofread_reports(project_path: str):
    output_path, store = _load_proofread_store(project_path)
    current = None
    if store.path.exists() or store.has_report_content():
        current = {
            "file": store.path.name,
            "is_archive": False,
            "run": store.run,
            "summary": store._build_summary(),
        }
    return {
        "project_path": output_path,
        "current": current,
        "archives": store.archive_summaries(),
    }

@app.get("/api/proofread/suggestions")
async def get_proofread_suggestions(project_path: str, report_file: Optional[str] = None):
    output_path, store = _load_proofread_store(project_path, report_file)
    cache_manager = get_cache_manager()
    cache_path = os.path.join(output_path, "cache", "AinieeCacheData.json")
    if not os.path.isfile(cache_path):
        raise HTTPException(status_code=404, detail="Cache file not found")
    cache_manager.load_from_file(output_path, interactive_recovery=False)
    store.refresh_conflicts(cache_manager.project)
    return _proofread_report_payload(store)

@app.get("/api/proofread/locate")
async def locate_proofread_item(
    project_path: str,
    file_path: str,
    text_index: int,
    page_size: int = 15,
):
    output_path = resolve_cache_output_path(project_path)
    cache_manager = get_cache_manager()
    cache_manager.load_from_file(output_path, interactive_recovery=False)
    normalized_page_size = min(max(int(page_size or 15), 1), 500)
    position = 0
    for current_file_path, cache_file in cache_manager.project.files.items():
        for item in cache_file.items:
            if not item.source_text or not item.source_text.strip():
                continue
            if current_file_path == file_path and int(item.text_index) == int(text_index):
                return {
                    "global_index": position,
                    "page": position // normalized_page_size + 1,
                    "row_index": position % normalized_page_size,
                    "file_path": file_path,
                    "text_index": text_index,
                }
            position += 1
    raise HTTPException(status_code=404, detail="Cache item not found")

def _run_proofread_review_action(
    suggestion_id: str,
    action: str,
    request: ProofreadSuggestionActionRequest,
):
    from ModuleFolders.Service.Proofreader import ProofreadReviewService

    output_path, store = _load_proofread_store(request.project_path, request.report_file)
    cache_manager = get_cache_manager()
    cache_manager.load_from_file(output_path, interactive_recovery=False)
    service = ProofreadReviewService(
        store,
        cache_manager.project,
        save_project=lambda: _save_loaded_cache(output_path),
    )
    actions = {
        "reject": service.reject,
        "ignore": service.ignore,
        "restore": service.restore,
        "delete": service.delete,
    }
    if action != "accept" and action not in actions:
        raise HTTPException(status_code=400, detail="Unsupported proofread action")
    try:
        if action == "accept":
            result = service.accept(
                suggestion_id,
                client="web",
                allow_manual_edit_override=request.allow_manual_edit_override,
            )
        else:
            result = actions[action](suggestion_id, client="web")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    status_code = 200 if result.success else 409
    return JSONResponse(
        status_code=status_code,
        content={
            "success": result.success,
            "suggestion_id": result.suggestion_id,
            "status": str(result.status) if result.status else None,
            "message": result.message,
            "report": _proofread_report_payload(store),
        },
    )

@app.post("/api/proofread/suggestions/{suggestion_id}/{action}")
async def review_proofread_suggestion(
    suggestion_id: str,
    action: str,
    request: ProofreadSuggestionActionRequest,
):
    return _run_proofread_review_action(suggestion_id, action, request)

@app.post("/api/proofread/undo")
async def undo_proofread_action(request: ProofreadSuggestionActionRequest):
    from ModuleFolders.Service.Proofreader import ProofreadReviewService

    output_path, store = _load_proofread_store(request.project_path, request.report_file)
    cache_manager = get_cache_manager()
    cache_manager.load_from_file(output_path, interactive_recovery=False)
    service = ProofreadReviewService(
        store,
        cache_manager.project,
        save_project=lambda: _save_loaded_cache(output_path),
    )
    result = service.undo_last(client="web")
    return JSONResponse(
        status_code=200 if result.success else 409,
        content={
            "success": result.success,
            "suggestion_id": result.suggestion_id,
            "status": str(result.status) if result.status else None,
            "message": result.message,
            "report": _proofread_report_payload(store),
        },
    )

@app.put("/api/proofread/review-state")
async def update_proofread_review_state(request: ProofreadReviewStateRequest):
    _, store = _load_proofread_store(request.project_path, request.report_file)
    if request.current_suggestion_id is not None:
        if request.current_suggestion_id and not any(
            item.suggestion_id == request.current_suggestion_id
            for item in store.suggestions
        ):
            raise HTTPException(status_code=400, detail="Unknown proofread suggestion")
        store.review_state["current_suggestion_id"] = request.current_suggestion_id
    if request.active_filter is not None:
        if request.active_filter not in {
            "pending", "ignored", "conflict", "accepted", "rejected", "completed", "all"
        }:
            raise HTTPException(status_code=400, detail="Invalid proofread review filter")
        store.review_state["active_filter"] = request.active_filter
    store.review_state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    store.review_state["updated_by"] = "web"
    store.save()
    return {"success": True, "review_state": store.review_state}

@app.get("/api/proofread/status")
async def get_proofread_status():
    """Get AI proofread task status"""
    return _proofread_state

@app.post("/api/proofread/start")
async def start_proofread(request: ProofreadStartRequest, background_tasks: BackgroundTasks):
    """Start AI proofread task"""
    global _proofread_state

    if _proofread_state["running"]:
        raise HTTPException(status_code=400, detail="Proofread task already running")

    cache_manager = get_cache_manager()

    output_path = resolve_cache_output_path(request.project_path)

    # Validate cache file exists
    cache_file_path = os.path.join(output_path, "cache", "AinieeCacheData.json")
    if not os.path.exists(cache_file_path):
        raise HTTPException(status_code=404, detail=f"Cache file not found: {cache_file_path}")

    # Load cache if not already loaded or different path
    try:
        cache_manager.load_from_file(output_path, interactive_recovery=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load cache: {e}")

    if not hasattr(cache_manager, 'project') or not cache_manager.project.files:
        raise HTTPException(status_code=400, detail="No cache data loaded")

    from ModuleFolders.Service.Proofreader import ProofreadSuggestionStore, normalize_suggestion_mode

    config = load_config_sync()
    report_mode = str(request.report_mode or config.get("proofread_report_mode", "archive"))
    try:
        suggestion_mode = normalize_suggestion_mode(
            request.suggestion_mode or config.get("proofread_suggestion_mode", "proofread")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store = ProofreadSuggestionStore(output_path)
    store.load()
    if report_mode == "overwrite" and store.has_report_content() and not request.overwrite_confirmed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "proofread_overwrite_confirmation_required",
                "summary": store._build_summary(),
            },
        )
    try:
        archive_limit = max(0, int(config.get("proofread_archive_limit", 20)))
    except (TypeError, ValueError):
        archive_limit = 20
    archive_path = store.prepare_new_run(
        mode=report_mode,
        provider=str(config.get("platform", "") or ""),
        model=str(config.get("model", "") or ""),
        archive_limit=archive_limit,
        suggestion_mode=suggestion_mode,
    )

    # Reset state
    _proofread_state = {
        "running": True,
        "progress": 0,
        "total": 0,
        "issues": [],
        "tokens_used": 0,
        "error": None,
        "completed": False,
        "output_path": output_path,
        "report_file": store.path.name,
        "archive_path": str(archive_path) if archive_path else None,
        "suggestion_mode": suggestion_mode,
    }

    # Start background task
    background_tasks.add_task(run_proofread_task)

    return {"status": "started"}

@app.post("/api/proofread/stop")
async def stop_proofread():
    """Stop AI proofread task"""
    global _proofread_state
    _proofread_state["running"] = False
    return {"status": "stopped"}

@app.post("/api/proofread/accept")
async def accept_proofread_issue(issue_id: str):
    """Accept a persistent suggestion through the shared review service."""
    from ModuleFolders.Service.Proofreader import ProofreadReviewService

    issue = next(
        (
            item
            for item in _proofread_state.get("issues", [])
            if str(item.get("id", "")) == str(issue_id)
            or str(item.get("suggestion_id", "")) == str(issue_id)
        ),
        None,
    )
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    suggestion_id = str(issue.get("suggestion_id") or issue.get("id") or "")
    output_path = str(_proofread_state.get("output_path") or "")
    if not suggestion_id or not output_path:
        raise HTTPException(status_code=400, detail="Persistent proofread report is unavailable")

    report_file = str(_proofread_state.get("report_file") or "")
    if report_file == "ProofreadSuggestions.json":
        report_file = ""
    resolved_output_path, store = _load_proofread_store(output_path, report_file or None)
    cache_manager = get_cache_manager()
    cache_manager.load_from_file(resolved_output_path, interactive_recovery=False)
    service = ProofreadReviewService(
        store,
        cache_manager.project,
        save_project=lambda: _save_loaded_cache(resolved_output_path),
    )
    try:
        result = service.accept(suggestion_id, client="web-legacy")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not result.success:
        raise HTTPException(status_code=409, detail=result.message)

    issue["accepted"] = True
    issue["status"] = str(result.status)
    return {
        "status": "accepted",
        "suggestion_id": suggestion_id,
        "report": _proofread_report_payload(store),
    }

class ProofreadSingleRequest(BaseModel):
    project_path: str
    file_path: str
    text_index: int
    translation: Optional[str] = None

@app.post("/api/proofread/single_check")
async def check_single_line(request: ProofreadSingleRequest):
    """
    On-demand check for a single line with context.
    Used when user clicks 'AI Analyze' on a specific line in editor.
    """
    try:
        from ModuleFolders.Service.Proofreader.AIProofreader import AIProofreader
        from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig

        cache_manager = get_cache_manager()
        
        # Ensure project is loaded
        if not hasattr(cache_manager, 'project') or not cache_manager.project.files:
             # Try to load if project is not in memory
             try:
                 load_cache_sync(request.project_path)
             except:
                 raise HTTPException(status_code=400, detail="Project cache not loaded")

        cache_file = cache_manager.project.get_file(request.file_path)
        if not cache_file:
            raise HTTPException(status_code=404, detail="File not found in cache")
        
        item = cache_file.get_item(request.text_index)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        # Determine target translation: prefer the one sent from web UI (editing state)
        target_translation = request.translation
        if target_translation is None:
            target_translation = item.translated_text or item.polished_text

        # Get context (5 lines before and after)
        list_idx = -1
        for idx, it in enumerate(cache_file.items):
            if it.text_index == request.text_index:
                list_idx = idx
                break
        
        if list_idx == -1:
             raise HTTPException(status_code=404, detail="Item index error")

        context_lines = 5
        start = max(0, list_idx - context_lines)
        end = min(len(cache_file.items), list_idx + context_lines + 1)
        
        context_parts = []
        for i in range(start, end):
            if i != list_idx:
                ctx_item = cache_file.items[i]
                if ctx_item.source_text:
                    # Provide original translation as context if available
                    ctx_trans = ctx_item.translated_text or ctx_item.polished_text or ""
                    context_parts.append(f"[{i}] {ctx_item.source_text[:60]} -> {ctx_trans[:40]}")
        
        context_str = "\n".join(context_parts)
        
        # Load Config
        config = load_config_sync()
        ai_proofreader = AIProofreader(config)
        
        # Run Check
        result = ai_proofreader.proofread_single(
            source=item.source_text,
            translation=target_translation,
            glossary=config.get("prompt_dictionary_data", []),
            context=context_str,
            world_building=config.get("world_building_content", ""),
            writing_style=config.get("writing_style_content", ""),
            characterization=config.get("characterization_data", [])
        )
        
        if not result.has_issues:
            return {"has_issues": False, "message": "AI分析后发现此行并无问题"}
        
        return {
            "has_issues": True,
            "issues": [
                {
                    "type": iss.type,
                    "severity": iss.severity,
                    "description": iss.description,
                    "suggestion": iss.suggestion,
                    "corrected_translation": result.corrected_translation
                } for iss in result.issues
            ],
            "corrected_translation": result.corrected_translation
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

def load_cache_sync(project_path: str):
    """Helper to load cache synchronously if needed"""
    cm = get_cache_manager()
    input_path = os.path.normpath(project_path.strip())
    if input_path.endswith("AinieeCacheData.json"):
        output_path = os.path.dirname(os.path.dirname(input_path))
    elif input_path.endswith("cache"):
        output_path = os.path.dirname(input_path)
    else:
        output_path = input_path
    cm.load_from_file(output_path)


@app.post("/api/proofread/clear")
async def clear_proofread_issues():
    """Clear all proofread issues"""
    global _proofread_state
    _proofread_state["issues"] = []
    _proofread_state["completed"] = False
    return {"status": "cleared"}

def load_config_sync() -> Dict[str, Any]:
    """Synchronously load the merged configuration."""
    return _load_active_config_payload()

def run_proofread_task():
    """Generate persistent proofread suggestions using the shared CLI pipeline."""
    global _proofread_state

    try:
        import concurrent.futures

        from ModuleFolders.Infrastructure.RequestLimiter.RequestLimiter import RequestLimiter
        from ModuleFolders.Infrastructure.TaskConfig.TaskConfig import TaskConfig
        from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
        from ModuleFolders.Service.Proofreader import (
            ProofreadRawResponseStore,
            ProofreadSuggestionStore,
            build_proofread_batch,
            collect_suggestion_items,
            normalize_suggestion_mode,
        )
        from ModuleFolders.Service.Proofreader.ProofreadSuggestionTask import ProofreadSuggestionTask

        cache_manager = get_cache_manager()
        config = load_config_sync()
        output_path = str(_proofread_state.get("output_path") or "")
        store = ProofreadSuggestionStore(output_path)
        store.load()
        raw_response_store = (
            ProofreadRawResponseStore(output_path)
            if bool(config.get("proofread_save_raw_responses", False))
            else None
        )
        suggestion_mode = normalize_suggestion_mode(
            _proofread_state.get("suggestion_mode")
            or store.run.get("suggestion_mode", "proofread")
        )
        items = collect_suggestion_items(cache_manager.project)
        if not items:
            _proofread_state["running"] = False
            _proofread_state["completed"] = True
            store.mark_generation_completed()
            return

        task_config = TaskConfig()
        task_config.load_config_from_dict(config)
        task_config.prepare_for_translation(TaskType.TRANSLATION)
        limiter = RequestLimiter()
        limiter.set_limit(
            config.get("tpm_limit", getattr(task_config, "tpm_limit", 100000)),
            config.get("rpm_limit", getattr(task_config, "rpm_limit", 60)),
        )
        try:
            batch_size = max(1, int(config.get("proofread_batch_size", config.get("lines_limit", 20))))
        except (TypeError, ValueError):
            batch_size = 20
        try:
            context_count = max(0, int(config.get("proofread_context_lines", 5)))
        except (TypeError, ValueError):
            context_count = 5

        batches = [
            build_proofread_batch(items, index, batch_size)
            for index in range((len(items) + batch_size - 1) // batch_size)
        ]
        _proofread_state["total"] = len(batches)
        glossary = config.get("prompt_dictionary_data", []) or []
        state_lock = threading.Lock()

        def context_for(index: int) -> list[dict]:
            start = max(0, index * batch_size - context_count)
            end = index * batch_size
            return [
                {
                    "source": item.get("source_text", ""),
                    "translation": item.get("translation", ""),
                }
                for item in items[start:end]
            ]

        def run_batch(index: int):
            if not _proofread_state["running"]:
                return None
            task = ProofreadSuggestionTask(
                task_config,
                limiter,
                batches[index],
                glossary=glossary,
                context_lines=context_for(index),
                suggestion_mode=suggestion_mode,
            )
            return task.run()

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(task_config.actual_thread_counts or 1)),
            thread_name_prefix="web-proofread-suggestion",
        ) as executor:
            futures = [executor.submit(run_batch, index) for index in range(len(batches))]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result is None:
                    continue
                with state_lock:
                    parsed = result.get("result")
                    if not result.get("skip", True) and parsed is not None:
                        store.add_batch_result(parsed)
                    raw_response = result.get("raw_response")
                    if raw_response_store is not None and isinstance(raw_response, dict):
                        try:
                            raw_response_store.append(
                                {
                                    **raw_response,
                                    "run_id": store.run.get("run_id", ""),
                                    "provider": store.run.get("provider", ""),
                                    "model": store.run.get("model", ""),
                                }
                            )
                        except Exception as exc:
                            print(f"Failed to save proofread raw response: {exc}")
                    _proofread_state["progress"] += 1
                    _proofread_state["tokens_used"] += int(result.get("prompt_tokens", 0) or 0)
                    _proofread_state["tokens_used"] += int(result.get("completion_tokens", 0) or 0)
                    _proofread_state["issues"] = [
                        {
                            "id": item.suggestion_id,
                            "suggestion_id": item.suggestion_id,
                            "text_index": item.text_index,
                            "file_path": item.file_path,
                            "source": item.source_text,
                            "original_translation": item.current_translation,
                            "corrected_translation": item.suggested_translation,
                            "issue_type": item.issue_type,
                            "severity": item.severity,
                            "description": item.reason,
                            "accepted": str(item.status) == "accepted",
                            "status": str(item.status),
                        }
                        for item in store.suggestions
                    ]

        _proofread_state["running"] = False
        _proofread_state["completed"] = True
        store.mark_generation_completed()

    except Exception as e:
        _proofread_state["running"] = False
        _proofread_state["error"] = str(e)
        import traceback
        traceback.print_exc()

# --- Queue Management API ---

def get_queue_manager():
    """Get QueueManager instance"""
    try:
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager
        return QueueManager()
    except ImportError:
        raise HTTPException(status_code=500, detail="QueueManager not available")

@app.get("/api/queue")
async def get_queue(request: Request):
    """Get all tasks in the queue with accurate processing status"""
    try:
        qm = get_queue_manager()

        # 清理过期的锁定状态
        if hasattr(qm, 'cleanup_stale_locks'):
            qm.cleanup_stale_locks()

        tasks = []

        for idx, task in enumerate(qm.tasks):
            # Ensure all tasks have the locked attribute and default status
            if not hasattr(task, 'locked'):
                task.locked = False
            if not hasattr(task, 'status'):
                task.status = "waiting"

            # 获取准确的处理状态
            is_actually_processing = False
            processing_info = None
            if hasattr(qm, 'is_task_actually_processing'):
                is_actually_processing = qm.is_task_actually_processing(idx)

            if hasattr(qm, 'get_task_processing_status'):
                processing_info = qm.get_task_processing_status(idx)

            # 如果任务被标记为locked但实际上没有在处理，则解锁
            if task.locked and not is_actually_processing:
                if hasattr(qm, 'stop_task_processing'):
                    qm.stop_task_processing(idx)
                    task.locked = False

            try:
                if hasattr(task, "to_task_spec"):
                    public_task_type = task.to_task_spec().task_type
                else:
                    public_task_type = normalize_task_name(getattr(task, "task_type", None))
                task_contract_error = None
            except (AttributeError, TaskContractError, TypeError, ValueError) as exc:
                public_task_type = "invalid"
                task_contract_error = str(exc)

            task_dict = {
                "task_type": public_task_type,
                "input_path": task.input_path,
                "output_path": getattr(task, "output_path", ""),
                "profile": getattr(task, "profile", ""),
                "rules_profile": getattr(task, "rules_profile", ""),
                "source_lang": getattr(task, "source_lang", ""),
                "target_lang": getattr(task, "target_lang", ""),
                "project_type": getattr(task, "project_type", ""),
                "platform": getattr(task, "platform", ""),
                "api_url": getattr(task, "api_url", ""),
                "api_key": "",
                "api_key_configured": bool(getattr(task, "api_key", "")),
                "model": getattr(task, "model", ""),
                "threads": getattr(task, "threads", None),
                "retry": getattr(task, "retry", None),
                "timeout": getattr(task, "timeout", None),
                "rounds": getattr(task, "rounds", None),
                "pre_lines": getattr(task, "pre_lines", None),
                "lines_limit": getattr(task, "lines_limit", None),
                "tokens_limit": getattr(task, "tokens_limit", None),
                "think_depth": getattr(task, "think_depth", ""),
                "thinking_budget": getattr(task, "thinking_budget", None),
                "failover": getattr(task, "failover", None),
                "resume": getattr(task, "resume", False),
                "polish_mode": getattr(task, "polish_mode", None),
                "status": getattr(task, "status", "waiting"),
                "locked": getattr(task, "locked", False),

                # 新增：准确的处理状态信息
                "is_actually_processing": is_actually_processing,
                "is_processing": getattr(task, "is_processing", False),
                "process_start_time": getattr(task, "process_start_time", None),
                "last_activity_time": getattr(task, "last_activity_time", None)
            }
            if task_contract_error:
                task_dict["task_contract_error"] = task_contract_error
            tasks.append(task_dict)
        if is_mcp_request(request):
            return sanitize_data_for_mcp(tasks, path="/api/queue")

        return tasks
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/queue")
async def add_to_queue(item: QueueTaskItem, request: Request):
    """Add a new task to the queue"""
    try:
        if is_mcp_request(request) and item.api_key == MCP_SECRET_PLACEHOLDER:
            raise HTTPException(
                status_code=400,
                detail="A redacted MCP secret placeholder cannot be used as a new queue API key.",
            )
        if item.api_key:
            raise HTTPException(
                status_code=422,
                detail=(
                    "api_key cannot be stored in a queue file. "
                    "Use a profile or run the task directly."
                ),
            )

        qm = get_queue_manager()
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueTaskItem as QueueTaskItemImpl

        task = QueueTaskItemImpl(**item.to_task_spec().to_queue_fields())

        # Ensure the task has proper defaults
        task.status = "waiting"
        task.locked = False

        qm.add_task(task)
        return {"success": True}
    except TaskContractError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/queue/{index}")
async def remove_from_queue(index: int):
    """Remove a task from the queue"""
    try:
        qm = get_queue_manager()
        if qm.remove_task(index):
            return {"success": True}
        else:
            raise HTTPException(status_code=400, detail="Failed to remove task")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/queue/{index}")
async def update_queue_item(index: int, item: QueueTaskUpdate, request: Request):
    """Update a task in the queue"""
    try:
        qm = get_queue_manager()
        if index < 0 or index >= len(qm.tasks):
            raise HTTPException(status_code=400, detail="Invalid task index")

        task = qm.tasks[index]

        fields_to_update = set(item.model_fields_set)
        preserve_api_key = item.api_key == "" or (
            is_mcp_request(request) and item.api_key == MCP_SECRET_PLACEHOLDER
        )
        if preserve_api_key:
            fields_to_update.discard("api_key")
        elif "api_key" in fields_to_update and item.api_key:
            raise HTTPException(
                status_code=422,
                detail=(
                    "api_key cannot be stored in a queue file. "
                    "Use a profile or run the task directly."
                ),
            )

        existing_payload = task.to_task_spec().to_mapping(
            include_none=True,
            include_api_key=True,
        )
        queue_payload = dict(existing_payload)
        for field_name in fields_to_update:
            queue_payload[field_name] = getattr(item, field_name)
        if "lines" in fields_to_update:
            legacy_lines = queue_payload.pop("lines")
            if "lines_limit" in fields_to_update and queue_payload.get("lines_limit") != legacy_lines:
                raise TaskContractError("Conflicting values for lines and lines_limit")
            queue_payload["lines_limit"] = legacy_lines
            fields_to_update.discard("lines")
            fields_to_update.add("lines_limit")
        if "tokens" in fields_to_update:
            legacy_tokens = queue_payload.pop("tokens")
            if "tokens_limit" in fields_to_update and queue_payload.get("tokens_limit") != legacy_tokens:
                raise TaskContractError("Conflicting values for tokens and tokens_limit")
            queue_payload["tokens_limit"] = legacy_tokens
            fields_to_update.discard("tokens")
            fields_to_update.add("tokens_limit")
        if (
            "task_type" in fields_to_update
            and "polish_mode" not in fields_to_update
            and normalize_task_name(item.task_type) == "translate"
        ):
            queue_payload["polish_mode"] = None
            fields_to_update.add("polish_mode")
        if "lines_limit" in fields_to_update and queue_payload.get("lines_limit") is not None:
            queue_payload["tokens_limit"] = None
            fields_to_update.add("tokens_limit")
        if "tokens_limit" in fields_to_update and queue_payload.get("tokens_limit") is not None:
            queue_payload["lines_limit"] = None
            fields_to_update.add("lines_limit")
        if preserve_api_key:
            queue_payload["api_key"] = existing_payload.get("api_key")
        # TaskSpec 用 None 表示无覆盖，而 model_fields_set 保留显式 null 的清空语义。
        queue_fields = TaskSpec.from_mapping(queue_payload).to_queue_fields(
            include_api_key=True
        )
        for field_name, value in queue_fields.items():
            if field_name not in fields_to_update:
                continue
            setattr(task, field_name, value)
        if "resume" in fields_to_update:
            # 自动化工作流需要区分“未设置”与显式 false；null 用于恢复默认语义。
            task.resume_explicit = item.resume is not None

        if qm.update_task(index, task):
            return {"success": True}
        else:
            raise HTTPException(status_code=400, detail="Failed to update task")
    except TaskContractError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/queue/clear")
async def clear_queue():
    """Clear all tasks from the queue"""
    try:
        qm = get_queue_manager()
        if qm.clear_tasks():
            return {"success": True}
        else:
            raise HTTPException(status_code=400, detail="Failed to clear queue")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/queue/run")
async def run_queue():
    """Start queue execution"""
    try:
        qm = get_queue_manager()

        if qm.is_running:
            return {"success": True, "message": "Queue is already running"}

        # Backward compatibility if QueueManager later provides a direct runner
        if hasattr(qm, 'run_queue'):
            qm.run_queue()
            return {"success": True}

        # Preferred path: delegate to host CLI callback
        if queue_handlers.get("run"):
            try:
                started = queue_handlers["run"]()
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))
            if not started:
                raise HTTPException(status_code=400, detail="Failed to start queue")
            return {"success": True}

        raise HTTPException(
            status_code=503,
            detail="Queue execution requires host integration. Start Web Server from AiNiee CLI."
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/queue/edit_file")
async def edit_queue_file():
    """Open queue file in external editor"""
    try:
        qm = get_queue_manager()
        # Check if method exists, if not provide fallback
        if hasattr(qm, 'open_queue_editor'):
            qm.open_queue_editor()
        else:
            # Fallback: could open file with system editor
            import subprocess
            import sys
            if sys.platform.startswith('win'):
                subprocess.run(['notepad', qm.queue_file])
            else:
                subprocess.run(['xdg-open', qm.queue_file])
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/queue/raw")
async def get_queue_raw(request: Request):
    """Get raw queue JSON content"""
    try:
        qm = get_queue_manager()
        # Read the file directly if method doesn't exist
        if hasattr(qm, 'get_queue_json'):
            content = qm.get_queue_json()
        else:
            # Fallback: read file content directly
            try:
                with open(qm.queue_file, 'r', encoding='utf-8') as f:
                    content = f.read()
            except FileNotFoundError:
                content = "[]"
        if is_mcp_request(request):
            return {"content": sanitize_json_text_for_mcp(content)}

        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/queue/raw")
async def save_queue_raw(request: QueueRawRequest, http_request: Request):
    """Save raw queue JSON content"""
    try:
        qm = get_queue_manager()
        content_to_save = request.content

        if is_mcp_request(http_request):
            current_content = "[]"
            if hasattr(qm, 'get_queue_json'):
                current_content = qm.get_queue_json()
            else:
                try:
                    with open(qm.queue_file, 'r', encoding='utf-8') as f:
                        current_content = f.read()
                except FileNotFoundError:
                    current_content = "[]"

            content_to_save = restore_redacted_json_text(content_to_save, current_content)
            try:
                parsed_content = json.loads(content_to_save)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON format")

            _ensure_no_mcp_secret_placeholder(parsed_content, "Queue raw content")

        if hasattr(qm, 'load_from_json'):
            qm.load_from_json(content_to_save)
        else:
            # Fallback: save to file directly and reload
            try:
                import rapidjson as json
                # Validate JSON first
                json.loads(content_to_save)
                # Save to file
                with open(qm.queue_file, 'w', encoding='utf-8') as f:
                    f.write(content_to_save)
                # Reload tasks
                qm.load_tasks()
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON format")
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/queue/{from_index}/move")
async def move_queue_item(from_index: int, request: QueueMoveRequest):
    """Move a task to a different position"""
    try:
        qm = get_queue_manager()

        if from_index < 0 or from_index >= len(qm.tasks) or request.to_index < 0 or request.to_index >= len(qm.tasks):
            raise HTTPException(status_code=400, detail="Invalid task index")

        # Check if tasks can be modified
        if not qm.can_modify_task(from_index):
            raise HTTPException(status_code=400, detail="Source task is locked")

        # Check range between from and to for locked tasks
        start, end = min(from_index, request.to_index), max(from_index, request.to_index)
        for i in range(start, end + 1):
            if i != from_index and not qm.can_modify_task(i):
                raise HTTPException(status_code=400, detail="Cannot move task due to locked tasks in path")

        # Use QueueManager's move_task method
        if qm.move_task(from_index, request.to_index):
            return {"success": True}
        else:
            raise HTTPException(status_code=400, detail="Failed to move task")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/queue/reorder")
async def reorder_queue(request: QueueReorderRequest):
    """Reorder tasks according to new order"""
    try:
        qm = get_queue_manager()
        if len(request.new_order) != len(qm.tasks):
            raise HTTPException(status_code=400, detail="New order length doesn't match queue length")

        # Use QueueManager's reorder_tasks method if available
        if hasattr(qm, 'reorder_tasks'):
            if qm.reorder_tasks(request.new_order):
                return {"success": True}
            else:
                raise HTTPException(status_code=400, detail="Failed to reorder tasks")
        else:
            # Fallback: manual reorder
            # Validate indices first
            for i in request.new_order:
                if i < 0 or i >= len(qm.tasks):
                    raise HTTPException(status_code=400, detail="Invalid task index in new order")

            # Reorder tasks according to new order
            new_tasks = [qm.tasks[i] for i in request.new_order]
            qm.tasks = new_tasks
            qm.save_tasks()
            return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Static File Serving for the React Frontend ---

# This will serve the built React app (index.html, JS, CSS files)
# The React app should be built into a `dist` directory inside `Tools/WebServer`
dist_path = os.path.join(WEB_SERVER_PATH, 'dist')

if os.path.exists(dist_path):
    app.mount("/", StaticFiles(directory=dist_path, html=True), name="static")

@app.get("/")
async def serve_index():
    """Serves the main index.html file of the React app."""
    index_path = os.path.join(dist_path, 'index.html')
    if not os.path.exists(index_path):
        # Fallback for development mode where `dist` might not exist
        return {"message": "AiNiee Backend is running. Frontend `dist` directory not found."}
    return FileResponse(index_path)

# --- Main Server Runner Function (to be called from ainiee_cli.py) ---

class StoppableServer(uvicorn.Server):
    def install_signal_handlers(self):
        pass

    @property
    def is_running(self):
        return self.started and not self.should_exit

_current_server: Optional[StoppableServer] = None


def _publish_internal_worker_environment(internal_api_url: str) -> None:
    os.environ["AINIEE_INTERNAL_API_URL"] = internal_api_url
    os.environ[INTERNAL_AUTH_ENV] = INTERNAL_API_TOKEN

def stop_server():
    """Stops the running uvicorn server and any active tasks."""
    global _current_server
    if _current_server:
        # 1. Stop any running subprocess task
        task_manager.stop_task()
        # 2. Tell uvicorn to exit
        _current_server.should_exit = True
        _current_server = None

def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    monitor_mode: bool = False,
    log_level: str = "info",
    allow_remote_access: bool = False,
):
    """Starts the FastAPI server in a separate thread."""
    global SYSTEM_MODE, REMOTE_ACCESS_ENABLED, _current_server
    ensure_bind_allowed(host, allow_remote_access, "WebServer")
    REMOTE_ACCESS_ENABLED = allow_remote_access is True
    SYSTEM_MODE = "monitor" if monitor_mode else "full"
    
    # 动态记录 WebServer 的地址，以便子进程上报数据
    task_manager.api_url = f"http://{host}:{port}"
    task_manager.internal_api_url = f"http://127.0.0.1:{port}"
    _publish_internal_worker_environment(task_manager.internal_api_url)
    
    try:
        # MCP stdio 模式需要绝对安静的 stdout，因此这里允许调用方下调 uvicorn 日志级别。
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=log_level,
            access_log=(log_level.lower() != "critical"),
        )
        _current_server = StoppableServer(config)

        def server_task():
            _current_server.run()

        # Running in a daemon thread allows the main TUI to exit cleanly
        thread = threading.Thread(target=server_task, daemon=True)
        thread.start()
        return thread
    except ImportError:
        # This should ideally be handled before calling run_server
        print("Error: Uvicorn is required to run the web server.")
        return None
