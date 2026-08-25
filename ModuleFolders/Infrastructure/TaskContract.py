"""跨 CLI、Web、Queue 与 Skills 的任务输入协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


TASK_API_KEY_ENV = "AINIEE_WEB_TASK_API_KEY"

TRANSLATE = "translate"
POLISH = "polish"
ALL_IN_ONE = "all_in_one"
EXPORT = "export"
QUEUE = "queue"

CANONICAL_TASK_TYPES = (TRANSLATE, POLISH, ALL_IN_ONE, EXPORT, QUEUE)
QUEUE_TASK_TYPES = (TRANSLATE, POLISH, ALL_IN_ONE)
QUEUE_TASK_OVERRIDE_FIELDS = (
    "input_path",
    "output_path",
    "profile",
    "rules_profile",
    "source_lang",
    "target_lang",
    "project_type",
    "resume",
    "platform",
    "model",
    "api_url",
    "api_key",
    "failover",
    "threads",
    "retry",
    "timeout",
    "rounds",
    "pre_lines",
    "lines_limit",
    "tokens_limit",
    "think_depth",
    "thinking_budget",
    "polish_mode",
)

_TASK_TYPE_ALIASES = {
    TRANSLATE: TRANSLATE,
    "translation": TRANSLATE,
    "manga": TRANSLATE,
    POLISH: POLISH,
    "polishing": POLISH,
    ALL_IN_ONE: ALL_IN_ONE,
    "translate_and_polish": ALL_IN_ONE,
    EXPORT: EXPORT,
    QUEUE: QUEUE,
}
_TASK_TYPE_CODES = {
    1: TRANSLATE,
    2: POLISH,
    3: ALL_IN_ONE,
    1000: TRANSLATE,
    2000: POLISH,
    4000: ALL_IN_ONE,
}
_QUEUE_CODES_BY_TASK = {
    TRANSLATE: 1000,
    POLISH: 2000,
    ALL_IN_ONE: 4000,
}
_THINK_DEPTH_NAMES = {"minimal", "low", "medium", "high", "xhigh", "max"}
_POLISH_MODES = {
    "translated_text_polish",
    "source_text_polish",
}
_POLISH_MODE_ALIASES = {
    "translated": "translated_text_polish",
    "translation": "translated_text_polish",
    "translated_text": "translated_text_polish",
    "translated-text": "translated_text_polish",
    "translated-text-polish": "translated_text_polish",
    "target": "translated_text_polish",
    "draft": "translated_text_polish",
    "source": "source_text_polish",
    "source_text": "source_text_polish",
    "source-text": "source_text_polish",
    "source-text-polish": "source_text_polish",
    "original": "source_text_polish",
}

TASK_CONTRACT_INPUT_FIELDS = frozenset(
    {
        "task",
        "task_type",
        "run_all_in_one",
        "input_path",
        "output_path",
        "profile",
        "rules_profile",
        "source_lang",
        "target_lang",
        "project_type",
        "resume",
        "queue_file",
        "platform",
        "model",
        "api_url",
        "api_key",
        "failover",
        "threads",
        "retry",
        "timeout",
        "rounds",
        "pre_lines",
        "lines",
        "tokens",
        "lines_limit",
        "tokens_limit",
        "think_depth",
        "thinking_budget",
        "polish_mode",
        "manga",
    }
)


class TaskContractError(ValueError):
    """任务入口数据无法归一化时抛出。"""


def normalize_task_name(value: Any) -> str:
    """把公共字符串、旧队列代码和旧短代码转换为规范任务名。"""
    if isinstance(value, bool):
        raise TaskContractError(f"Unsupported task type: {value!r}")

    if isinstance(value, int):
        try:
            return _TASK_TYPE_CODES[value]
        except KeyError as exc:
            raise TaskContractError(f"Unsupported task type: {value!r}") from exc

    text = str(value or "").strip().lower()
    if text.isdigit():
        try:
            return _TASK_TYPE_CODES[int(text)]
        except KeyError as exc:
            raise TaskContractError(f"Unsupported task type: {value!r}") from exc
    try:
        return _TASK_TYPE_ALIASES[text]
    except KeyError as exc:
        raise TaskContractError(f"Unsupported task type: {value!r}") from exc


def task_type_to_queue_code(value: Any) -> int:
    task_name = normalize_task_name(value)
    try:
        return _QUEUE_CODES_BY_TASK[task_name]
    except KeyError as exc:
        raise TaskContractError(f"Task type {task_name!r} cannot be queued") from exc


def select_task_contract_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    """从带自动化元数据的对象中提取任务协议字段。"""
    return {key: data[key] for key in TASK_CONTRACT_INPUT_FIELDS if key in data}


def _optional_string(value: Any, field_name: str, *, preserve: bool = False) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TaskContractError(f"{field_name} must be a string")
    if not value.strip():
        return None
    return value if preserve else value.strip()


def _bool_value(value: Any, field_name: str, *, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "on", "yes", "1"}:
            return True
        if normalized in {"false", "off", "no", "0"}:
            return False
    raise TaskContractError(f"{field_name} must be a boolean")


def _int_value(
    value: Any,
    field_name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise TaskContractError(f"{field_name} must be an integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value.strip())
        except ValueError as exc:
            raise TaskContractError(f"{field_name} must be an integer") from exc
    else:
        raise TaskContractError(f"{field_name} must be an integer")

    if minimum is not None and result < minimum:
        raise TaskContractError(f"{field_name} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise TaskContractError(f"{field_name} must be <= {maximum}")
    return result


def _limit_value(value: Any, field_name: str, minimum: int, maximum: int) -> int | None:
    result = _int_value(value, field_name)
    if result is None:
        return None
    return max(minimum, min(maximum, result))


def _resolve_limit_alias(
    data: Mapping[str, Any],
    canonical_name: str,
    alias_name: str,
    minimum: int,
    maximum: int,
) -> int | None:
    canonical = _limit_value(data.get(canonical_name), canonical_name, minimum, maximum)
    alias = _limit_value(data.get(alias_name), alias_name, minimum, maximum)
    if canonical is not None and alias is not None and canonical != alias:
        raise TaskContractError(
            f"Conflicting values for {canonical_name} and legacy alias {alias_name}"
        )
    return canonical if canonical is not None else alias


def _think_depth(value: Any) -> str | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise TaskContractError("think_depth must be a named level or an integer from 0 to 10000")
    if isinstance(value, int):
        if 0 <= value <= 10000:
            return value
        raise TaskContractError("think_depth must be between 0 and 10000")
    if not isinstance(value, str):
        raise TaskContractError("think_depth must be a named level or an integer from 0 to 10000")

    normalized = value.strip().lower()
    if normalized in _THINK_DEPTH_NAMES:
        return normalized
    if normalized.isdigit():
        return _think_depth(int(normalized))
    raise TaskContractError(f"Unsupported think_depth: {value!r}")


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """一次任务的不可变覆盖参数，不包含队列运行状态。"""

    task_type: str
    input_path: str | None = None
    output_path: str | None = None
    profile: str | None = None
    rules_profile: str | None = None
    source_lang: str | None = None
    target_lang: str | None = None
    project_type: str | None = None
    resume: bool = False
    queue_file: str | None = None
    platform: str | None = None
    model: str | None = None
    api_url: str | None = None
    api_key: str | None = None
    failover: bool | None = None
    threads: int | None = None
    retry: int | None = None
    timeout: int | None = None
    rounds: int | None = None
    pre_lines: int | None = None
    lines_limit: int | None = None
    tokens_limit: int | None = None
    think_depth: str | int | None = None
    thinking_budget: int | None = None
    polish_mode: str | None = None
    manga: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, strict: bool = True) -> "TaskSpec":
        if not isinstance(data, Mapping):
            raise TaskContractError("Task payload must be a mapping")
        if strict:
            unknown = sorted(set(data) - TASK_CONTRACT_INPUT_FIELDS)
            if unknown:
                raise TaskContractError(f"Unknown task fields: {', '.join(unknown)}")

        task_value = data.get("task_type")
        legacy_task_value = data.get("task")
        if task_value is None and legacy_task_value is None:
            raise TaskContractError("task or task_type is required")

        normalized_task = normalize_task_name(
            task_value if task_value is not None else legacy_task_value
        )
        if task_value is not None and legacy_task_value is not None:
            legacy_task = normalize_task_name(legacy_task_value)
            legacy_all_in_one = _bool_value(
                data.get("run_all_in_one"), "run_all_in_one", default=False
            )
            compatible_legacy_pair = (
                bool(legacy_all_in_one)
                and {normalized_task, legacy_task} == {TRANSLATE, ALL_IN_ONE}
            )
            if normalized_task != legacy_task and not compatible_legacy_pair:
                raise TaskContractError("task and task_type describe different tasks")

        run_all_in_one = _bool_value(
            data.get("run_all_in_one"), "run_all_in_one", default=False
        )
        if run_all_in_one:
            if normalized_task not in {TRANSLATE, ALL_IN_ONE}:
                raise TaskContractError(
                    "run_all_in_one is only compatible with translate or all_in_one"
                )
            normalized_task = ALL_IN_ONE

        manga_alias = any(
            isinstance(value, str) and value.strip().lower() == "manga"
            for value in (task_value, legacy_task_value)
        )
        manga = bool(_bool_value(data.get("manga"), "manga", default=False) or manga_alias)
        if manga and normalized_task != TRANSLATE:
            raise TaskContractError("manga is only supported for translate tasks")

        input_path = _optional_string(data.get("input_path"), "input_path", preserve=True)
        if normalized_task != QUEUE and input_path is None:
            raise TaskContractError(f"input_path is required for {normalized_task} tasks")

        queue_file = _optional_string(data.get("queue_file"), "queue_file", preserve=True)
        if queue_file is not None and normalized_task != QUEUE:
            raise TaskContractError("queue_file is only supported for queue tasks")

        lines_limit = _resolve_limit_alias(data, "lines_limit", "lines", 1, 100)
        tokens_limit = _resolve_limit_alias(data, "tokens_limit", "tokens", 400, 16000)
        if lines_limit is not None and tokens_limit is not None:
            raise TaskContractError("lines_limit and tokens_limit are mutually exclusive")

        polish_mode = _optional_string(data.get("polish_mode"), "polish_mode")
        if polish_mode is not None:
            polish_mode = polish_mode.lower().replace(" ", "_")
            polish_mode = _POLISH_MODE_ALIASES.get(polish_mode, polish_mode)
            if polish_mode not in _POLISH_MODES:
                raise TaskContractError(f"Unsupported polish_mode: {polish_mode!r}")
            if normalized_task not in {POLISH, ALL_IN_ONE}:
                raise TaskContractError("polish_mode is only supported for polish tasks")

        return cls(
            task_type=normalized_task,
            input_path=input_path,
            output_path=_optional_string(data.get("output_path"), "output_path", preserve=True),
            profile=_optional_string(data.get("profile"), "profile"),
            rules_profile=_optional_string(data.get("rules_profile"), "rules_profile"),
            source_lang=_optional_string(data.get("source_lang"), "source_lang"),
            target_lang=_optional_string(data.get("target_lang"), "target_lang"),
            project_type=_optional_string(data.get("project_type"), "project_type"),
            resume=bool(_bool_value(data.get("resume"), "resume", default=False)),
            queue_file=queue_file,
            platform=_optional_string(data.get("platform"), "platform"),
            model=_optional_string(data.get("model"), "model"),
            api_url=_optional_string(data.get("api_url"), "api_url"),
            api_key=_optional_string(data.get("api_key"), "api_key", preserve=True),
            failover=_bool_value(data.get("failover"), "failover"),
            threads=_int_value(data.get("threads"), "threads", minimum=0),
            retry=_int_value(data.get("retry"), "retry", minimum=0),
            timeout=_int_value(data.get("timeout"), "timeout", minimum=1),
            rounds=_int_value(data.get("rounds"), "rounds", minimum=1),
            pre_lines=_int_value(data.get("pre_lines"), "pre_lines", minimum=0),
            lines_limit=lines_limit,
            tokens_limit=tokens_limit,
            think_depth=_think_depth(data.get("think_depth")),
            thinking_budget=_int_value(
                data.get("thinking_budget"), "thinking_budget", minimum=-1
            ),
            polish_mode=polish_mode,
            manga=manga,
        )

    def to_mapping(
        self,
        *,
        include_none: bool = False,
        include_api_key: bool = False,
    ) -> dict[str, Any]:
        result = {
            "task_type": self.task_type,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "profile": self.profile,
            "rules_profile": self.rules_profile,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "project_type": self.project_type,
            "resume": self.resume,
            "queue_file": self.queue_file,
            "platform": self.platform,
            "model": self.model,
            "api_url": self.api_url,
            "failover": self.failover,
            "threads": self.threads,
            "retry": self.retry,
            "timeout": self.timeout,
            "rounds": self.rounds,
            "pre_lines": self.pre_lines,
            "lines_limit": self.lines_limit,
            "tokens_limit": self.tokens_limit,
            "think_depth": self.think_depth,
            "thinking_budget": self.thinking_budget,
            "polish_mode": self.polish_mode,
            "manga": self.manga,
        }
        if include_api_key:
            result["api_key"] = self.api_key
        if include_none:
            return result
        return {key: value for key, value in result.items() if value is not None}

    def to_queue_task_type(self) -> int:
        return task_type_to_queue_code(self.task_type)

    def to_queue_fields(self, *, include_api_key: bool = True) -> dict[str, Any]:
        """生成当前 QueueTaskItem 构造器使用的兼容字段。"""
        if self.manga:
            raise TaskContractError("Manga tasks are not supported by the task queue")
        if self.queue_file is not None:
            raise TaskContractError("queue_file cannot be stored inside a queue item")

        mapping = self.to_mapping(include_none=True, include_api_key=include_api_key)
        result = {
            field: mapping[field]
            for field in QUEUE_TASK_OVERRIDE_FIELDS
            if include_api_key or field != "api_key"
        }
        result["task_type"] = self.to_queue_task_type()
        return result


def build_cli_args(
    task: TaskSpec | Mapping[str, Any],
    *,
    non_interactive: bool = False,
    web_mode: bool = False,
) -> list[str]:
    """生成不含可见凭据的 CLI argv 片段。"""
    spec = task if isinstance(task, TaskSpec) else TaskSpec.from_mapping(task)
    args = [spec.task_type]
    if spec.input_path is not None:
        args.append(spec.input_path)

    value_options = (
        ("--output", spec.output_path),
        ("--profile", spec.profile),
        ("--rules-profile", spec.rules_profile),
        ("--queue-file", spec.queue_file),
        ("--source", spec.source_lang),
        ("--target", spec.target_lang),
        ("--type", spec.project_type),
        ("--threads", spec.threads),
        ("--retry", spec.retry),
        ("--rounds", spec.rounds),
        ("--timeout", spec.timeout),
        ("--polish-mode", spec.polish_mode),
        ("--platform", spec.platform),
        ("--model", spec.model),
        ("--api-url", spec.api_url),
        ("--think-depth", spec.think_depth),
        ("--thinking-budget", spec.thinking_budget),
        ("--lines", spec.lines_limit),
        ("--tokens", spec.tokens_limit),
        ("--pre-lines", spec.pre_lines),
    )
    for option, value in value_options:
        if value is not None:
            args.extend([option, str(value)])

    if spec.resume:
        args.append("--resume")
    if spec.failover is not None:
        args.extend(["--failover", "on" if spec.failover else "off"])
    if spec.manga:
        args.append("--manga")
    if non_interactive:
        args.append("--yes")
    if web_mode:
        args.append("--web-mode")
    return args
