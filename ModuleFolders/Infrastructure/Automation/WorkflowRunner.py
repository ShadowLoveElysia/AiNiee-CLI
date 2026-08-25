"""
Automation workflow runner.

This module keeps watch/scheduler managers focused on triggering events while
the runner adapts those events to existing AiNiee task, queue, and glossary
analysis capabilities.
"""
import copy
import os
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

import rapidjson as json
from rich.console import Console

from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.TaskConfig.ConfigProfileService import (
    atomic_write_json,
    default_rules_payload,
    normalize_rules_payload,
    resolve_profile_path,
    sanitize_profile_name,
)
from ModuleFolders.Infrastructure.TaskConfig.TaskType import TaskType
from ModuleFolders.Infrastructure.TaskContract import (
    QUEUE_TASK_TYPES,
    TASK_CONTRACT_INPUT_FIELDS,
    TaskContractError,
    TaskSpec,
    select_task_contract_fields,
    task_type_to_queue_code,
)
from ModuleFolders.Infrastructure.Automation.AutomationPaths import automation_glossary_dir_for


console = Console()


AUTOMATION_TASK_METADATA_FIELDS = frozenset(
    {
        "auto_start",
        "automation_progress_file",
        "automation_run_id",
        "automation_worker_pid",
        "defer_auto_start",
        "event_type",
        "extra",
        "folder_batch_id",
        "folder_batch_index",
        "folder_batch_total",
        "is_processing",
        "last_activity_time",
        "locked",
        "process_start_time",
        "resume_explicit",
        "rule_id",
        "run_queue",
        "series_batch_volumes",
        "series_container_path",
        "series_incremental",
        "series_initial_volume",
        "series_key",
        "series_missing_volumes",
        "series_volume",
        "source",
        "status",
        "task_id",
        "task_name",
        "trigger_detected_at",
        "trigger_file_name",
        "trigger_file_path",
        "trigger_type",
        "workflow_description",
        "workflow_steps",
    }
)


WORKFLOW_STEP_LABELS = {
    "extract_glossary": "AI glossary analysis",
    "translate": "Translation",
    "polish": "Polishing",
    "all_in_one": "Translation + polishing",
    "queue": "Add to queue",
    "run_queue": "Run queue",
}


_WORKFLOW_TASK_FIELDS = frozenset(TASK_CONTRACT_INPUT_FIELDS) | {
    "type",
    "output_root",
    "output_mode",
}
_WORKFLOW_GLOSSARY_FIELDS = frozenset(
    {
        "type",
        "analysis_percent",
        "analysis_lines",
        "analysis_mode",
        "prompt_file",
        "translate_during_analysis",
        "new",
        "replace",
        "min_frequency",
        "save_mode",
        "series_incremental",
        "source_volume",
        "source_label",
    }
)
_WORKFLOW_STEP_FIELDS = {
    "translate": _WORKFLOW_TASK_FIELDS,
    "polish": _WORKFLOW_TASK_FIELDS,
    "all_in_one": _WORKFLOW_TASK_FIELDS,
    "queue": _WORKFLOW_TASK_FIELDS,
    "run_queue": frozenset({"type"}),
    "extract_glossary": _WORKFLOW_GLOSSARY_FIELDS,
}


class AutomationPartialCompletion(RuntimeError):
    def __init__(self, message: str = "Automation task completed partially"):
        super().__init__(message)


def normalize_task_type(value: Any) -> int:
    try:
        return task_type_to_queue_code(value or "translate")
    except TaskContractError as exc:
        raise ValueError(str(exc)) from exc


def task_type_to_step_type(value: Any) -> str:
    normalized = normalize_task_type(value)
    if normalized == TaskType.POLISH:
        return "polish"
    if normalized == TaskType.TRANSLATE_AND_POLISH:
        return "all_in_one"
    return "translate"


def default_workflow_steps(task_type: Any = "translation", auto_start: bool = True) -> List[dict]:
    if not auto_start:
        return [{"type": "queue", "task_type": task_type_to_step_type(task_type)}]
    return [{"type": task_type_to_step_type(task_type)}]


def normalize_workflow_steps(
    steps: Optional[Iterable[dict]],
    task_type: Any = "translation",
    auto_start: bool = True,
) -> List[dict]:
    normalized_steps: List[dict] = []
    for step in steps or []:
        if not isinstance(step, dict):
            raise TaskContractError("Workflow step must be an object")
        step_type = str(step.get("type") or "").strip().lower()
        if not step_type:
            raise TaskContractError("Workflow step type is required")
        if step_type in {"translation", "translate"}:
            step_type = "translate"
        elif step_type in {"polishing", "polish"}:
            step_type = "polish"
        elif step_type in {"translate_and_polish", "all_in_one"}:
            step_type = "all_in_one"
        elif step_type in {"glossary", "analysis", "extract_glossary"}:
            step_type = "extract_glossary"
        elif step_type in {"add_to_queue", "queue"}:
            step_type = "queue"
        elif step_type in {"start_queue", "run_queue"}:
            step_type = "run_queue"
        else:
            raise TaskContractError(f"Unsupported workflow step type: {step_type!r}")

        prepared = dict(step)
        prepared["type"] = step_type
        unknown = sorted(set(prepared) - _WORKFLOW_STEP_FIELDS[step_type])
        if unknown:
            raise TaskContractError(
                f"Unknown fields for workflow step {step_type!r}: {', '.join(unknown)}"
            )
        if step_type == "queue":
            prepared["task_type"] = task_type_to_step_type(prepared.get("task_type", task_type))
        normalized_steps.append(prepared)

    return normalized_steps or default_workflow_steps(task_type, auto_start)


def workflow_step_label(step_type: str, i18n=None) -> str:
    step_type = str(step_type or "").strip()
    key = f"workflow_step_{step_type}"
    if i18n is not None:
        try:
            label = i18n.get(key)
            if label and label != key:
                return label
        except Exception:
            pass
    return WORKFLOW_STEP_LABELS.get(step_type, step_type or "?")


def describe_workflow_steps(steps: Iterable[dict], i18n=None) -> str:
    labels = []
    for step in steps or []:
        step_type = str(step.get("type") or "").strip()
        if step_type == "all_in_one":
            labels.append(workflow_step_label("translate", i18n))
            labels.append(workflow_step_label("polish", i18n))
        else:
            labels.append(workflow_step_label(step_type, i18n))
    return " -> ".join(labels)


def is_queue_only_workflow(steps: Iterable[dict]) -> bool:
    step_types = [str(step.get("type") or "").strip().lower() for step in steps or [] if isinstance(step, dict)]
    return bool(step_types) and all(step_type in {"queue", "run_queue"} for step_type in step_types)


class WorkflowRunner:
    """Execute configured automation workflow steps against a host CLIMenu."""

    def __init__(self, host, progress_reporter=None):
        self.host = host
        self.progress_reporter = progress_reporter

    def run(self, task_config: dict) -> bool:
        task_config = self._normalize_task_config(task_config)
        input_path = task_config.get("input_path") or ""
        steps = normalize_workflow_steps(
            task_config.get("workflow_steps"),
            task_config.get("task_type", "translation"),
            task_config.get("auto_start", True),
        )
        steps = self._prepare_series_glossary_steps(steps, task_config)

        original_active_profile = getattr(self.host, "active_profile_name", "default")
        original_rules_profile = getattr(self.host, "active_rules_profile_name", "default")
        original_root_config = copy.deepcopy(getattr(self.host, "root_config", {}))
        original_config = copy.deepcopy(getattr(self.host, "config", {}))
        original_runtime_overrides = copy.deepcopy(
            getattr(self.host, "runtime_config_overrides", {})
        )
        workflow_context: Dict[str, Any] = {}

        try:
            self._apply_profile_context(task_config)

            for index, step in enumerate(steps, 1):
                step = self._with_task_defaults(step, task_config)
                step = self._with_workflow_context(step, workflow_context)
                step_type = step.get("type")
                self._report_step(index, len(steps), step_type)
                self._log("info", f"Workflow step {index}/{len(steps)}: {step_type}")
                if step_type == "extract_glossary":
                    self._run_glossary_step(input_path, step, task_config, workflow_context)
                elif step_type == "translate":
                    self._run_task_step(
                        TaskType.TRANSLATION,
                        input_path,
                        step,
                        task_config,
                        resume=step.get("resume", task_config.get("resume", False)),
                    )
                elif step_type == "polish":
                    self._run_task_step(
                        TaskType.POLISH,
                        input_path,
                        step,
                        task_config,
                        resume=step.get("resume", task_config.get("resume", True)),
                    )
                elif step_type == "all_in_one":
                    self._run_all_in_one_step(input_path, step, task_config)
                elif step_type == "queue":
                    self._run_queue_add_step(input_path, task_config, step)
                elif step_type == "run_queue":
                    self._run_queue_step()
                else:
                    raise ValueError(f"Unsupported workflow step: {step_type}")

            return True
        finally:
            self.host.active_profile_name = original_active_profile
            self.host.active_rules_profile_name = original_rules_profile
            self.host.root_config = original_root_config
            self.host.config = original_config
            self.host.runtime_config_overrides = original_runtime_overrides

    @staticmethod
    def _normalize_task_config(task_config: dict) -> dict:
        """对自动化执行字段做统一校验，保留工作流运行元数据。"""
        if not isinstance(task_config, dict):
            raise TaskContractError("Automation task config must be a mapping")
        unknown = sorted(
            set(task_config) - TASK_CONTRACT_INPUT_FIELDS - AUTOMATION_TASK_METADATA_FIELDS
        )
        if unknown:
            raise TaskContractError(f"Unknown automation task fields: {', '.join(unknown)}")
        payload = select_task_contract_fields(task_config)
        resume_was_provided = payload.get("resume") is not None
        if "task_type" not in payload and "task" not in payload:
            payload["task_type"] = "translation"
        payload.setdefault("input_path", task_config.get("input_path", ""))
        spec = TaskSpec.from_mapping(payload)
        if spec.task_type not in QUEUE_TASK_TYPES or spec.manga:
            raise TaskContractError(
                f"Task type {spec.task_type!r} is not supported by automation workflows"
            )
        normalized = dict(task_config)
        normalized.update(spec.to_mapping(include_none=True, include_api_key=True))
        normalized["task_type"] = spec.task_type
        if not resume_was_provided:
            normalized.pop("resume", None)
        return normalized

    @staticmethod
    def is_enqueue_only(task_config: dict) -> bool:
        steps = normalize_workflow_steps(
            task_config.get("workflow_steps"),
            task_config.get("task_type", "translation"),
            task_config.get("auto_start", True),
        )
        steps = WorkflowRunner._prepare_series_glossary_steps(steps, task_config)
        return bool(steps) and steps[-1].get("type") == "queue"

    def _report_step(self, index: int, total: int, step_type: str):
        if not self.progress_reporter:
            return
        label = workflow_step_label(step_type, getattr(self.host, "i18n", None))
        self.progress_reporter.update_workflow_step(index, total, step_type, label)

    def _with_task_defaults(self, step: dict, task_config: dict) -> dict:
        prepared = dict(step)
        if (
            prepared.get("type") in {"translate", "polish", "all_in_one"}
            and task_config.get("output_path")
            and not prepared.get("output_path")
            and not prepared.get("output_root")
        ):
            prepared["output_path"] = task_config.get("output_path")
            prepared["output_mode"] = "exact"
        return prepared

    def _with_workflow_context(self, step: dict, workflow_context: dict) -> dict:
        prepared = dict(step)
        rules_profile = workflow_context.get("rules_profile")
        if rules_profile and prepared.get("type") in {"translate", "polish", "all_in_one", "queue"}:
            prepared.setdefault("rules_profile", rules_profile)
        return prepared

    def _apply_profile_context(self, task_config: dict):
        profile = task_config.get("profile")
        rules_profile = task_config.get("rules_profile")
        if profile or rules_profile:
            self.host.load_config(
                active_profile_name=profile or getattr(self.host, "active_profile_name", None),
                active_rules_profile_name=rules_profile or getattr(self.host, "active_rules_profile_name", None),
            )
        self._apply_task_overrides(task_config)

    def _apply_task_overrides(self, task_config: dict):
        cfg = getattr(self.host, "config", {})
        if not isinstance(cfg, dict):
            return

        if task_config.get("source_lang"):
            cfg["source_language"] = task_config.get("source_lang")
        if task_config.get("target_lang"):
            cfg["target_language"] = task_config.get("target_lang")
        if task_config.get("project_type"):
            cfg["translation_project"] = task_config.get("project_type")
        if task_config.get("output_path"):
            cfg["label_output_path"] = task_config.get("output_path")

        if task_config.get("platform"):
            cfg["target_platform"] = task_config.get("platform")
        if task_config.get("api_url"):
            cfg["base_url"] = task_config.get("api_url")
        if task_config.get("api_key"):
            cfg["api_key"] = task_config.get("api_key")
            target_platform = cfg.get("target_platform")
            if target_platform and target_platform in cfg.get("platforms", {}):
                cfg["platforms"][target_platform]["api_key"] = task_config.get("api_key")
        if task_config.get("model"):
            cfg["model"] = task_config.get("model")

        if task_config.get("threads") is not None:
            cfg["user_thread_counts"] = task_config.get("threads")
        if task_config.get("retry") is not None:
            cfg["retry_count"] = task_config.get("retry")
        if task_config.get("timeout") is not None:
            cfg["request_timeout"] = task_config.get("timeout")
        if task_config.get("rounds") is not None:
            cfg["round_limit"] = task_config.get("rounds")
        if task_config.get("pre_lines") is not None:
            cfg["pre_line_counts"] = task_config.get("pre_lines")

        if task_config.get("lines_limit") is not None:
            cfg["tokens_limit_switch"] = False
            cfg["lines_limit"] = task_config.get("lines_limit")
        if task_config.get("tokens_limit") is not None:
            cfg["tokens_limit_switch"] = True
            cfg["tokens_limit"] = max(400, int(task_config.get("tokens_limit")))

        if task_config.get("think_depth") is not None:
            cfg["think_depth"] = task_config.get("think_depth")
            target_platform = cfg.get("target_platform")
            if target_platform and target_platform in cfg.get("platforms", {}):
                cfg["platforms"][target_platform]["think_depth"] = task_config.get("think_depth")
        if task_config.get("thinking_budget") is not None:
            cfg["thinking_budget"] = task_config.get("thinking_budget")
            target_platform = cfg.get("target_platform")
            if target_platform and target_platform in cfg.get("platforms", {}):
                cfg["platforms"][target_platform]["thinking_budget"] = task_config.get("thinking_budget")

        if task_config.get("failover") is not None:
            cfg["enable_api_failover"] = bool(task_config.get("failover"))
        if task_config.get("polish_mode"):
            runtime_overrides = getattr(self.host, "runtime_config_overrides", {})
            if not isinstance(runtime_overrides, dict):
                runtime_overrides = {}
            runtime_overrides["polishing_mode_selection"] = task_config.get("polish_mode")
            self.host.runtime_config_overrides = runtime_overrides

        self._apply_dynamic_glossary_context(task_config)

    def _apply_dynamic_glossary_context(self, task_config: dict):
        cfg = getattr(self.host, "config", {})
        if not isinstance(cfg, dict):
            return

        volume = self._normalize_int(task_config.get("series_volume"))
        if volume is None:
            return

        trigger_path = task_config.get("trigger_file_path") or task_config.get("input_path") or ""
        input_path = task_config.get("input_path") or trigger_path
        cfg["dynamic_glossary_switch"] = True
        cfg["dynamic_glossary_series"] = task_config.get("series_key") or self._series_key_for(trigger_path or input_path, task_config)
        cfg["dynamic_glossary_volume"] = volume

        volume_map = cfg.get("dynamic_glossary_volume_map")
        if not isinstance(volume_map, dict):
            volume_map = {}
        for path in {trigger_path, input_path}:
            if not path:
                continue
            volume_map[path] = volume
            volume_map[os.path.abspath(path)] = volume
            volume_map[os.path.basename(os.path.normpath(path))] = volume
        cfg["dynamic_glossary_volume_map"] = volume_map

    @staticmethod
    def _prepare_series_glossary_steps(steps: List[dict], task_config: dict) -> List[dict]:
        if not task_config.get("series_incremental"):
            return steps
        prepared_steps = []
        for step in steps:
            prepared = dict(step)
            if prepared.get("type") == "extract_glossary":
                prepared["series_incremental"] = True
                if task_config.get("series_volume") is not None:
                    prepared.setdefault("source_volume", task_config.get("series_volume"))
                if task_config.get("series_volume") is not None:
                    prepared.setdefault("source_label", f"第{task_config.get('series_volume')}卷")
                elif task_config.get("series_key"):
                    prepared.setdefault("source_label", task_config.get("series_key"))
            prepared_steps.append(prepared)
        return prepared_steps

    def _run_glossary_step(self, input_path: str, step: dict, task_config: dict, workflow_context: dict):
        if not input_path:
            raise ValueError("Glossary analysis requires input_path")
        if not os.path.exists(input_path):
            raise FileNotFoundError(input_path)

        from ModuleFolders.Service.GlossaryAnalysis import GlossaryAnalyzer

        analyzer = GlossaryAnalyzer(self.host)
        trigger_path = task_config.get("trigger_file_path") or input_path
        source_volume = step.get("source_volume")
        if source_volume is None and step.get("series_incremental"):
            source_volume = self._series_volume_for(trigger_path)
        source_label = step.get("source_label") or self._source_label_for(input_path)
        if step.get("series_incremental") and source_volume is not None and not step.get("source_label"):
            source_label = f"第{source_volume}卷"

        series_profile_name = ""
        series_profile_created = False
        series_profile_seed = ""
        series_initial_run = False
        series_existing_volumes = set()
        normalized_source_volume = None
        if step.get("series_incremental"):
            series_profile_name, series_profile_created, series_profile_seed = self._activate_series_rules_profile(
                trigger_path,
                task_config,
            )
            series_existing_volumes = self._series_existing_volumes_from_config()
            normalized_source_volume = self._normalize_int(source_volume)
            self._report_series_glossary_status(
                series_profile_name,
                series_existing_volumes,
                task_config,
                normalized_source_volume,
            )
            if normalized_source_volume is not None and normalized_source_volume in series_existing_volumes:
                workflow_context["rules_profile"] = series_profile_name
                message = self._tr(
                    "automation_series_glossary_skip_existing",
                    "当前卷{}已包含在自动术语表“{}”中，跳过术语提取并直接绑定该术语表。",
                    source_label,
                    series_profile_name,
                )
                if self.progress_reporter:
                    self.progress_reporter.update(rules_profile=series_profile_name, message=message)
                self._log("info", message)
                return
            series_initial_run = self._is_initial_series_glossary_run(
                task_config,
                source_volume,
                series_profile_created,
                series_profile_seed,
            )
            analyzer = GlossaryAnalyzer(self.host)
            if self.progress_reporter:
                message = (
                    f"Auto series glossary profile initialized: {series_profile_name}"
                    if series_profile_created
                    else f"Auto series glossary profile loaded: {series_profile_name}"
                )
                if series_profile_seed:
                    message = f"{message} | seed: {series_profile_seed}"
                if series_initial_run:
                    message = f"{message} | initial volume: {source_label}"
                self.progress_reporter.update(rules_profile=series_profile_name, message=message)
            self._log(
                "info",
                (
                    f"Auto series glossary profile initialized: {series_profile_name}"
                    if series_profile_created
                    else f"Auto series glossary profile loaded: {series_profile_name}"
                )
                + (f" | seed: {series_profile_seed}" if series_profile_seed else "")
                + (f" | initial volume: {source_label}" if series_initial_run else ""),
            )

        analysis_new = bool(step.get("new", True))
        analysis_replace = bool(step.get("replace", True))
        if series_initial_run:
            analysis_new = False
            analysis_replace = False

        analysis_result = analyzer.execute_analysis(
            input_path,
            int(step.get("analysis_percent", 100) or 100),
            step.get("analysis_lines"),
            temp_config=None,
            analysis_mode=str(step.get("analysis_mode") or "full"),
            prompt_file=step.get("prompt_file") or None,
            translate_during_analysis=bool(step.get("translate_during_analysis", True)),
            new=analysis_new,
            replace=analysis_replace,
            source_label=source_label,
            source_volume=source_volume,
            existing_rules_context=None,
            output_dir=automation_glossary_dir_for(input_path),
        )
        if analysis_result is None:
            raise RuntimeError("Glossary analysis returned no result")

        min_frequency = int(step.get("min_frequency", 2) or 2)
        save_result = analyzer.filter_and_save(
            analysis_result,
            min_frequency,
            output_dir=automation_glossary_dir_for(input_path),
        )
        if save_result is None:
            raise RuntimeError("Glossary analysis did not produce savable rules")

        structured_rules = save_result.get("structured_rules")
        incremental_options = save_result.get("incremental_options")
        save_mode = str(step.get("save_mode") or "isolated")
        if save_mode == "isolated":
            if step.get("series_incremental") and series_profile_name:
                if structured_rules:
                    analyzer.save_structured_rules_directly(
                        structured_rules,
                        save_mode="import",
                        base_glossary_path=save_result.get("glossary_path"),
                        merge_options=incremental_options,
                    )
                elif save_result.get("glossary_data"):
                    analyzer.save_glossary_directly(
                        save_result["glossary_data"],
                        save_mode="import",
                        base_glossary_path=save_result.get("glossary_path"),
                        merge_options=incremental_options,
                    )
                workflow_context["rules_profile"] = series_profile_name
                if self.progress_reporter:
                    self.progress_reporter.update(
                        rules_profile=series_profile_name,
                        message=f"Auto series glossary profile updated: {series_profile_name}",
                    )
                self._log("info", f"Auto series glossary profile updated: {series_profile_name}")
                return

            profile_name = self._create_isolated_rules_profile(input_path, task_config, structured_rules, save_result.get("glossary_data"))
            workflow_context["rules_profile"] = profile_name
            if self.progress_reporter:
                self.progress_reporter.update(rules_profile=profile_name, message=f"Isolated glossary profile: {profile_name}")
            self._log("info", f"Isolated glossary profile created: {profile_name}")
            return

        if structured_rules:
            analyzer.save_structured_rules_directly(
                structured_rules,
                save_mode=save_mode,
                base_glossary_path=save_result.get("glossary_path"),
                merge_options=incremental_options,
            )
        elif save_result.get("glossary_data"):
            analyzer.save_glossary_directly(
                save_result["glossary_data"],
                save_mode=save_mode,
                base_glossary_path=save_result.get("glossary_path"),
                merge_options=incremental_options,
            )

    def _create_isolated_rules_profile(self, input_path: str, task_config: dict, structured_rules: dict, glossary_data: list) -> str:
        profile_name = self._isolated_rules_profile_name(input_path, task_config)
        rules_dir = getattr(self.host, "rules_profiles_dir", None) or os.path.join(getattr(self.host, "PROJECT_ROOT", os.getcwd()), "Resource", "rules_profiles")
        os.makedirs(rules_dir, exist_ok=True)
        profile_path, profile_name = resolve_profile_path(rules_dir, profile_name)
        if self._has_rules_payload(structured_rules):
            payload = {
                key: copy.deepcopy(value)
                for key, value in structured_rules.items()
                if key in default_rules_payload()
            }
        elif glossary_data:
            payload = {"prompt_dictionary_data": copy.deepcopy(glossary_data)}
        else:
            payload = {}
        payload = normalize_rules_payload(payload)
        atomic_write_json(profile_path, payload)
        return profile_name

    def _is_initial_series_glossary_run(
        self,
        task_config: dict,
        source_volume,
        series_profile_created: bool,
        series_profile_seed: str,
    ) -> bool:
        source_volume = self._normalize_int(source_volume)
        if source_volume is None:
            return False

        initial_volume = self._series_initial_volume_for(task_config)
        if initial_volume is not None:
            return source_volume == initial_volume

        return bool(series_profile_created and not series_profile_seed)

    def _series_initial_volume_for(self, task_config: dict):
        candidates = [
            task_config.get("series_initial_volume"),
            (task_config.get("extra") or {}).get("series_initial_volume") if isinstance(task_config.get("extra"), dict) else None,
        ]
        for candidate in candidates:
            volume = self._normalize_int(candidate)
            if volume is not None:
                return volume

        volumes = task_config.get("series_batch_volumes")
        if not volumes and isinstance(task_config.get("extra"), dict):
            volumes = task_config.get("extra", {}).get("series_batch_volumes")
        parsed_volumes = [
            volume
            for volume in (self._normalize_int(item) for item in (volumes or []))
            if volume is not None
        ]
        if parsed_volumes:
            return min(parsed_volumes)
        return None

    @staticmethod
    def _normalize_int(value):
        if value is None or value == "" or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _series_existing_volumes_from_config(self) -> set[int]:
        volumes = set()
        config = getattr(self.host, "config", {}) or {}
        for key in default_rules_payload():
            self._collect_series_volumes(config.get(key), volumes)
        return volumes

    def _collect_series_volumes(self, value, volumes: set[int]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"volume", "updated_volume", "source_volume"}:
                    volume = self._normalize_int(item)
                    if volume is not None:
                        volumes.add(volume)
                self._collect_series_volumes(item, volumes)
            return

        if isinstance(value, list):
            for item in value:
                self._collect_series_volumes(item, volumes)

    def _report_series_glossary_status(
        self,
        profile_name: str,
        existing_volumes: set[int],
        task_config: dict,
        source_volume,
    ) -> None:
        if not existing_volumes:
            return

        covered_volume = max(existing_volumes)
        target_volumes = self._series_target_volumes(task_config, source_volume)
        target_volume = max(target_volumes) if target_volumes else source_volume
        if target_volume is None:
            target_volume = covered_volume
        missing_volumes = [volume for volume in target_volumes if volume not in existing_volumes]
        message = self._tr(
            "automation_series_glossary_detected_existing",
            "检测到已有自动术语表“{}”，当前检测到术语表版本为{}，此次任务翻译覆盖到{}，有 {} 卷需要进行一次增量术语抓取。",
            profile_name,
            self._format_series_volume_label(covered_volume),
            self._format_series_volume_label(target_volume),
            len(missing_volumes),
        )
        if self.progress_reporter:
            self.progress_reporter.update(rules_profile=profile_name, message=message)
        self._log("info", message)

    def _series_target_volumes(self, task_config: dict, source_volume) -> List[int]:
        volumes = task_config.get("series_batch_volumes")
        if not volumes and isinstance(task_config.get("extra"), dict):
            volumes = task_config.get("extra", {}).get("series_batch_volumes")
        parsed = [
            volume
            for volume in (self._normalize_int(item) for item in (volumes or []))
            if volume is not None
        ]
        if not parsed:
            source_volume = self._normalize_int(source_volume)
            if source_volume is not None:
                parsed = [source_volume]
        return sorted(set(parsed))

    def _format_series_volume_label(self, volume) -> str:
        normalized = self._normalize_int(volume)
        if normalized is None:
            return "-"
        return self._tr("watch_series_volume_short", "第{}卷", normalized)

    def _tr(self, key: str, default: str, *args) -> str:
        value = None
        i18n = getattr(self.host, "i18n", None)
        if i18n is not None:
            try:
                value = i18n.get(key)
            except Exception:
                value = None
        if not value or value == key:
            value = default
        if args:
            try:
                return value.format(*args)
            except Exception:
                return value
        return value

    @staticmethod
    def _has_rules_payload(payload: dict) -> bool:
        if not isinstance(payload, dict):
            return False
        defaults = default_rules_payload()
        for key in defaults:
            value = payload.get(key)
            if isinstance(value, str):
                if value.strip():
                    return True
            elif value:
                return True
        return False

    def _isolated_rules_profile_name(self, input_path: str, task_config: dict) -> str:
        run_id = str(task_config.get("automation_run_id") or "").strip()
        label = self._source_label_for(input_path)
        if task_config.get("series_incremental") or self._task_has_series_glossary_step(task_config):
            return self._series_rules_profile_name(task_config.get("trigger_file_path") or input_path, task_config)
        else:
            base = f"auto_{run_id}_{label}" if run_id else f"auto_{label}_{uuid.uuid4().hex[:8]}"
        try:
            return sanitize_profile_name(base, allow_none=False)
        except ValueError:
            return f"auto_{uuid.uuid4().hex[:12]}"

    def _activate_series_rules_profile(self, input_path: str, task_config: dict) -> tuple[str, bool, str]:
        profile_name = self._series_rules_profile_name(input_path, task_config)
        rules_dir = getattr(self.host, "rules_profiles_dir", None) or os.path.join(getattr(self.host, "PROJECT_ROOT", os.getcwd()), "Resource", "rules_profiles")
        os.makedirs(rules_dir, exist_ok=True)
        profile_path, profile_name = resolve_profile_path(rules_dir, profile_name)

        created = False
        seed_name = ""
        if not os.path.exists(profile_path):
            seed_payload, seed_name = self._series_rules_seed_payload(input_path, task_config, rules_dir)
            atomic_write_json(profile_path, seed_payload)
            created = True

        self.host.load_config(
            active_profile_name=task_config.get("profile") or getattr(self.host, "active_profile_name", None),
            active_rules_profile_name=profile_name,
        )
        self._apply_task_overrides(task_config)
        return profile_name, created, seed_name

    def _series_rules_seed_payload(self, input_path: str, task_config: dict, rules_dir: str) -> tuple[dict, str]:
        series_key = self._series_key_for(input_path, task_config)
        for candidate in (series_key, task_config.get("rules_profile")):
            candidate = str(candidate or "").strip()
            if not candidate or candidate == "None" or candidate == self._series_rules_profile_name(input_path, task_config):
                continue
            try:
                candidate_path, candidate_name = resolve_profile_path(rules_dir, candidate, allow_none=True)
            except ValueError:
                continue
            if not candidate_path or not os.path.exists(candidate_path):
                continue
            try:
                with open(candidate_path, "r", encoding="utf-8-sig") as file:
                    data = json.load(file)
                if isinstance(data, dict):
                    return normalize_rules_payload(data), candidate_name
            except Exception:
                continue

        return self._rules_payload_from_current_config(), ""

    def _rules_payload_from_current_config(self) -> dict:
        config = getattr(self.host, "config", {}) or {}
        payload = {
            key: copy.deepcopy(config.get(key))
            for key in default_rules_payload()
            if key in config
        }
        return normalize_rules_payload(payload)

    def _series_rules_profile_name(self, input_path: str, task_config: dict = None) -> str:
        key = self._series_key_for(input_path, task_config)
        try:
            return sanitize_profile_name(f"Auto_{key}", allow_none=False)
        except ValueError:
            return f"Auto_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _task_has_series_glossary_step(task_config: dict) -> bool:
        for step in task_config.get("workflow_steps") or []:
            if isinstance(step, dict) and step.get("type") == "extract_glossary" and step.get("series_incremental"):
                return True
        return False

    def _run_task_step(
        self,
        task_type: int,
        input_path: str,
        step: dict,
        task_config: dict = None,
        resume: Any = False,
        contract_task_type: str | None = None,
    ):
        if not input_path:
            raise ValueError("Task step requires input_path")
        if not os.path.exists(input_path):
            raise FileNotFoundError(input_path)

        task_payload = select_task_contract_fields(task_config or {})
        task_payload.update(select_task_contract_fields(step))
        task_payload["task_type"] = contract_task_type or task_type_to_step_type(task_type)
        task_payload["input_path"] = input_path
        task_payload["resume"] = resume
        if task_payload["task_type"] == "translate":
            task_payload.pop("polish_mode", None)
        step_spec = TaskSpec.from_mapping(task_payload)

        previous_active_profile = getattr(self.host, "active_profile_name", None)
        previous_rules_profile = getattr(self.host, "active_rules_profile_name", None)
        previous_root_config = copy.deepcopy(getattr(self.host, "root_config", {}))
        previous_config = copy.deepcopy(getattr(self.host, "config", {}))
        previous_runtime_overrides = copy.deepcopy(
            getattr(self.host, "runtime_config_overrides", {})
        )

        try:
            if step_spec.profile or step_spec.rules_profile:
                self.host.load_config(
                    active_profile_name=step_spec.profile or previous_active_profile,
                    active_rules_profile_name=step_spec.rules_profile or previous_rules_profile,
                )
            self._apply_task_overrides(
                step_spec.to_mapping(include_none=True, include_api_key=True)
            )

            output_path = self._resolve_step_output_path(input_path, step)
            if output_path:
                self.host.config["label_output_path"] = output_path
                self.host.config["auto_set_output_path"] = False

            ok = self.host.run_task(
                task_type,
                target_path=input_path,
                continue_status=step_spec.resume,
                non_interactive=True,
                from_queue=True,
                skip_preflight=True,
                save_runtime_config=False,
                automation_progress=bool(self.progress_reporter),
            )
        finally:
            self.host.active_profile_name = previous_active_profile
            self.host.active_rules_profile_name = previous_rules_profile
            self.host.root_config = previous_root_config
            self.host.config = previous_config
            self.host.runtime_config_overrides = previous_runtime_overrides

        if not ok:
            partial_message = self._partial_message_from_reporter()
            if partial_message:
                raise AutomationPartialCompletion(partial_message)
            raise RuntimeError("Task blocked before start")

        partial_message = self._partial_message_from_reporter()
        if partial_message:
            raise AutomationPartialCompletion(partial_message)

    def _run_all_in_one_step(self, input_path: str, step: dict, task_config: dict = None):
        resume_explicit = "resume" in step or "resume" in (task_config or {})
        translate_resume = step.get("resume", (task_config or {}).get("resume", False))
        polish_resume = translate_resume if resume_explicit else True
        self._run_task_step(
            TaskType.TRANSLATION,
            input_path,
            step,
            task_config,
            resume=translate_resume,
            contract_task_type="all_in_one",
        )
        if Base.work_status != Base.STATUS.STOPING:
            polish_step = dict(step)
            polish_step["resume"] = polish_resume
            self._run_task_step(
                TaskType.POLISH,
                input_path,
                polish_step,
                task_config,
                resume=polish_resume,
                contract_task_type="all_in_one",
            )

    def _run_queue_add_step(self, input_path: str, task_config: dict, step: dict):
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager, QueueTaskItem

        if not input_path:
            raise ValueError("Queue step requires input_path")
        if not os.path.exists(input_path):
            raise FileNotFoundError(input_path)

        queue_manager = QueueManager()
        output_path = self._resolve_step_output_path(input_path, step) or task_config.get("output_path") or None
        task_payload = select_task_contract_fields(task_config)
        task_payload.update(select_task_contract_fields(step))
        task_payload.update(
            {
                "task_type": step.get("task_type", task_config.get("task_type", "translation")),
                "input_path": input_path,
                "output_path": output_path,
                "rules_profile": step.get("rules_profile") or task_config.get("rules_profile") or None,
            }
        )
        spec = TaskSpec.from_mapping(task_payload)
        queue_fields = spec.to_queue_fields()
        queue_manager.add_task(
            QueueTaskItem(
                **queue_fields,
                resume_explicit="resume" in task_payload,
                source=task_config.get("source"),
                rule_id=task_config.get("rule_id"),
                trigger_file_path=task_config.get("trigger_file_path"),
                trigger_file_name=task_config.get("trigger_file_name"),
                trigger_detected_at=task_config.get("trigger_detected_at"),
                series_incremental=task_config.get("series_incremental", False),
                series_key=task_config.get("series_key"),
                series_volume=task_config.get("series_volume"),
            )
        )

    def _run_queue_step(self):
        from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

        queue_manager = QueueManager()
        if not queue_manager.tasks:
            self._log("warning", "Task queue is empty")
            return
        if queue_manager.is_running:
            self._log("info", "Task queue is already running")
            return

        self.host._is_queue_mode = True
        queue_manager.start_queue(self.host, automation_background=bool(self.progress_reporter))
        try:
            while queue_manager.is_running:
                time.sleep(0.5)
        finally:
            self.host._is_queue_mode = False

    def _resolve_step_output_path(self, input_path: str, step: dict) -> str:
        output_path = str(step.get("output_path") or step.get("output_root") or "").strip()
        if not output_path:
            return ""

        mode = str(step.get("output_mode") or "subdir").strip().lower()
        if mode == "exact":
            return os.path.abspath(os.path.expanduser(output_path))

        base_name = os.path.basename(os.path.normpath(input_path))
        if os.path.isfile(input_path):
            base_name = os.path.splitext(base_name)[0]
        return os.path.join(os.path.abspath(os.path.expanduser(output_path)), f"{base_name}_AiNiee_Output")

    def _source_label_for(self, input_path: str) -> str:
        base_name = os.path.basename(os.path.normpath(input_path))
        if os.path.isfile(input_path):
            base_name = os.path.splitext(base_name)[0]
        return base_name or "Automation"

    def _partial_message_from_reporter(self) -> str:
        if not self.progress_reporter:
            return ""
        state = {}
        try:
            if hasattr(self.progress_reporter, "current_state"):
                state = self.progress_reporter.current_state()
            else:
                state = dict(getattr(self.progress_reporter, "state", {}) or {})
        except Exception:
            state = {}

        status = state.get("status")
        if status == "partial":
            return str(state.get("message") or "Automation task completed partially")

        current, total = self._progress_counts_from_state(state)
        if total > 0 and 0 < current < total:
            message = f"Translation items missing: {current}/{total}"
            try:
                self.progress_reporter.finish("partial", message)
            except Exception:
                pass
            return message
        return ""

    @staticmethod
    def _progress_counts_from_state(state: dict) -> tuple[int, int]:
        try:
            current = int(state.get("line") or state.get("completed") or 0)
            total = int(state.get("total_line") or state.get("total") or 0)
        except (TypeError, ValueError):
            return 0, 0
        return current, total

    def _series_volume_for(self, input_path: str):
        try:
            from ModuleFolders.Infrastructure.Automation.WatchManager import parse_series_volume
            return parse_series_volume(input_path).get("volume")
        except Exception:
            return None

    def _series_rules_context(self, input_path: str, source_volume, task_config: dict = None):
        rules_dir = getattr(self.host, "rules_profiles_dir", None) or os.path.join(getattr(self.host, "PROJECT_ROOT", os.getcwd()), "Resource", "rules_profiles")
        try:
            profile_path, _ = resolve_profile_path(
                rules_dir,
                self._series_rules_profile_name(input_path, task_config),
            )
            if os.path.exists(profile_path):
                with open(profile_path, "r", encoding="utf-8-sig") as file:
                    data = json.load(file)
                return data if isinstance(data, dict) else None
        except Exception:
            return None
        return None

    def _series_key_for(self, input_path: str, task_config: dict = None) -> str:
        task_config = task_config or {}
        if task_config.get("series_key"):
            return sanitize_profile_name(str(task_config["series_key"]), allow_none=False)
        try:
            from ModuleFolders.Infrastructure.Automation.WatchManager import parse_series_volume
            parsed = parse_series_volume(input_path)
            if parsed.get("series_key"):
                return sanitize_profile_name(str(parsed["series_key"]), allow_none=False)
        except Exception:
            pass
        return sanitize_profile_name(self._source_label_for(input_path), allow_none=False)

    def _log(self, level: str, message: str):
        ui = getattr(self.host, "ui", None)
        if ui is not None and hasattr(ui, "log"):
            try:
                ui.log(f"[{level}] {message}")
                return
            except Exception:
                pass
        console.print(message)
