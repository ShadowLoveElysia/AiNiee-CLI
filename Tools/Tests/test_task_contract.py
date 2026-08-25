import os
import json
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ModuleFolders.Infrastructure.TaskContract import (
    ALL_IN_ONE,
    POLISH,
    TRANSLATE,
    TaskContractError,
    TaskSpec,
    build_cli_args,
    normalize_task_name,
)


class TaskContractTests(unittest.TestCase):
    @staticmethod
    def _import_with_rapidjson(module_name: str):
        try:
            return __import__(module_name, fromlist=["*"])
        except ModuleNotFoundError as exc:
            if exc.name != "rapidjson":
                raise
        with patch.dict(sys.modules, {"rapidjson": json}):
            return __import__(module_name, fromlist=["*"])

    def test_task_type_aliases_and_queue_codes_are_equivalent(self):
        aliases = {
            TRANSLATE: ("translate", "translation", 1, "1", 1000, "1000"),
            POLISH: ("polish", "polishing", 2, "2", 2000, "2000"),
            ALL_IN_ONE: ("all_in_one", "translate_and_polish", 3, "3", 4000, "4000"),
        }
        for expected, values in aliases.items():
            for value in values:
                with self.subTest(value=value):
                    self.assertEqual(normalize_task_name(value), expected)

    def test_legacy_all_in_one_flag_and_canonical_task_match(self):
        legacy = TaskSpec.from_mapping(
            {
                "task": "translate",
                "run_all_in_one": True,
                "input_path": "H:/novel.txt",
            }
        )
        canonical = TaskSpec.from_mapping(
            {"task_type": "all_in_one", "input_path": "H:/novel.txt"}
        )
        self.assertEqual(legacy, canonical)

    def test_invalid_task_and_conflicting_all_in_one_are_rejected(self):
        with self.assertRaises(TaskContractError):
            TaskSpec.from_mapping({"task": "unknown", "input_path": "input.txt"})
        with self.assertRaises(TaskContractError):
            TaskSpec.from_mapping(
                {
                    "task": "polish",
                    "run_all_in_one": True,
                    "input_path": "input.txt",
                }
            )

    def test_limit_aliases_clamp_and_conflicts_fail(self):
        spec = TaskSpec.from_mapping(
            {"task": "translate", "input_path": "input.txt", "lines": 999}
        )
        self.assertEqual(spec.lines_limit, 100)

        with self.assertRaises(TaskContractError):
            TaskSpec.from_mapping(
                {
                    "task": "translate",
                    "input_path": "input.txt",
                    "lines": 10,
                    "lines_limit": 20,
                }
            )
        with self.assertRaises(TaskContractError):
            TaskSpec.from_mapping(
                {
                    "task": "translate",
                    "input_path": "input.txt",
                    "lines": 10,
                    "tokens": 1000,
                }
            )

    def test_think_depth_is_normalized_and_validated(self):
        named = TaskSpec.from_mapping(
            {"task": "translate", "input_path": "input.txt", "think_depth": "HIGH"}
        )
        numeric = TaskSpec.from_mapping(
            {"task": "translate", "input_path": "input.txt", "think_depth": "0"}
        )
        self.assertEqual(named.think_depth, "high")
        self.assertEqual(numeric.think_depth, 0)
        with self.assertRaises(TaskContractError):
            TaskSpec.from_mapping(
                {"task": "translate", "input_path": "input.txt", "think_depth": "ultra"}
            )

    def test_paths_are_preserved_as_single_cli_arguments(self):
        for input_path in (
            "H:\\Novel Project\\input.txt",
            "/tmp/Novel Project/input.txt",
        ):
            with self.subTest(input_path=input_path):
                spec = TaskSpec.from_mapping(
                    {"task": "translate", "input_path": input_path}
                )
                args = build_cli_args(spec)
                self.assertEqual(args[:2], [TRANSLATE, input_path])

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(TaskContractError):
            TaskSpec.from_mapping(
                {"task": "translate", "input_path": "input.txt", "typo_field": True}
            )

    def test_cli_args_forward_all_overrides_without_api_key(self):
        secret = "sk-contract-secret"
        spec = TaskSpec.from_mapping(
            {
                "task": "all_in_one",
                "input_path": "H:/Novel Project/input.txt",
                "output_path": "H:/Novel Project/output",
                "profile": "default",
                "rules_profile": "rules",
                "source_lang": "Japanese",
                "target_lang": "Chinese",
                "project_type": "Epub",
                "resume": True,
                "platform": "openai",
                "model": "gpt-test",
                "api_url": "https://example.invalid/v1",
                "api_key": secret,
                "failover": False,
                "threads": 8,
                "retry": 4,
                "rounds": 2,
                "timeout": 90,
                "pre_lines": 5,
                "tokens": 2048,
                "think_depth": "high",
                "thinking_budget": 4096,
                "polish_mode": "translated",
            }
        )
        args = build_cli_args(spec, non_interactive=True, web_mode=True)

        self.assertEqual(args[:2], ["all_in_one", "H:/Novel Project/input.txt"])
        for option, value in {
            "--type": "Epub",
            "--platform": "openai",
            "--think-depth": "high",
            "--thinking-budget": "4096",
            "--tokens": "2048",
            "--polish-mode": "translated_text_polish",
        }.items():
            self.assertEqual(args[args.index(option) + 1], value)
        self.assertIn("--resume", args)
        self.assertEqual(args[args.index("--failover") + 1], "off")
        self.assertIn("--yes", args)
        self.assertIn("--web-mode", args)
        self.assertNotIn("--api-key", args)
        self.assertNotIn(secret, args)

    def test_cli_namespace_uses_shared_task_normalization(self):
        command_module = self._import_with_rapidjson(
            "ModuleFolders.UserInterface.CommandModeRunner"
        )
        args = SimpleNamespace(
            task="2",
            input_path="input.txt",
            output_path=None,
            profile=None,
            rules_profile=None,
            queue_file=None,
            source_lang=None,
            target_lang=None,
            project_type=None,
            resume=False,
            platform=None,
            model=None,
            api_url=None,
            api_key=None,
            failover="off",
            threads=0,
            retry=None,
            timeout=None,
            rounds=None,
            pre_lines=0,
            lines=500,
            tokens=None,
            think_depth="0",
            thinking_budget=-1,
            polish_mode="source",
            manga=False,
        )

        spec = command_module.normalize_cli_task_args(args)

        self.assertEqual(spec.task_type, POLISH)
        self.assertEqual(args.task, POLISH)
        self.assertEqual(args.lines, 100)
        self.assertEqual(args.think_depth, 0)
        self.assertEqual(args.pre_lines, 0)
        self.assertEqual(args.failover, "off")
        self.assertEqual(args.polish_mode, "source_text_polish")

    def test_web_queue_and_skill_produce_same_task_spec(self):
        payload = {
            "task_type": "translation",
            "input_path": "/tmp/input.txt",
            "output_path": "/tmp/output",
            "platform": "custom",
            "threads": 0,
            "lines_limit": 20,
            "think_depth": "MAX",
            "thinking_budget": -1,
            "failover": True,
        }
        direct = TaskSpec.from_mapping(payload)
        queue_fields = direct.to_queue_fields()
        queue = TaskSpec.from_mapping(queue_fields)
        skill_payload = dict(payload)
        skill_payload.setdefault("task_type", "translate")
        skill = TaskSpec.from_mapping(skill_payload)

        self.assertEqual(
            direct.to_mapping(include_none=True),
            queue.to_mapping(include_none=True),
        )
        self.assertEqual(direct, skill)

    def test_skill_places_api_key_only_in_child_environment(self):
        secret = "sk-skill-secret"
        spec = TaskSpec.from_mapping(
            {"task": "translate", "input_path": "input.txt", "api_key": secret}
        )
        command = ["-m", "ainiee_cli", *build_cli_args(spec, non_interactive=True)]
        env = os.environ.copy()
        env["AINIEE_WEB_TASK_API_KEY"] = spec.api_key
        self.assertNotIn(secret, command)
        self.assertEqual(env["AINIEE_WEB_TASK_API_KEY"], secret)
        self.assertNotEqual(env, os.environ)

    def test_environment_api_key_is_runtime_only(self):
        command_module = self._import_with_rapidjson(
            "ModuleFolders.UserInterface.CommandModeRunner"
        )
        ainiee_cli = self._import_with_rapidjson("ainiee_cli")

        args = SimpleNamespace(api_key=None)
        worker_env = {"AINIEE_WEB_TASK_API_KEY": "temporary-worker-key"}
        ainiee_cli._consume_web_task_api_key(args, worker_env)

        host = SimpleNamespace(
            config={
                "api_key": "stored-key",
                "target_platform": "openai",
                "platforms": {"openai": {"api_key": "stored-platform-key"}},
            },
            runtime_config_overrides={},
        )
        command_module.CommandModeRunner(host)._apply_config_overrides(
            SimpleNamespace(
                source_lang=None,
                target_lang=None,
                output_path=None,
                project_type=None,
                threads=None,
                retry=None,
                timeout=None,
                rounds=None,
                pre_lines=None,
                polish_mode=None,
                lines=None,
                tokens=None,
                platform=None,
                model=None,
                api_url=None,
                api_key=args.api_key,
                _task_api_key_ephemeral=args._task_api_key_ephemeral,
                think_depth=None,
                thinking_budget=None,
                failover=None,
            )
        )

        self.assertNotIn("AINIEE_WEB_TASK_API_KEY", worker_env)
        self.assertEqual(host.config["api_key"], "stored-key")
        self.assertEqual(
            host.config["platforms"]["openai"]["api_key"],
            "stored-platform-key",
        )
        self.assertEqual(
            host.runtime_config_overrides["api_key"],
            "temporary-worker-key",
        )

    def test_queue_skill_run_executes_cli(self):
        self._import_with_rapidjson("ModuleFolders.Service.TaskQueue.QueueManager")
        queue_module = self._import_with_rapidjson("Tools.Skills.skills.queue_skill")
        completed = SimpleNamespace(returncode=0, stdout="queue complete", stderr="")

        with patch.object(queue_module.subprocess, "run", return_value=completed) as run:
            result = queue_module.QueueSkill().execute(
                {"action": "run", "queue_file": "H:/Queue Files/tasks.json"}
            )

        self.assertTrue(result.success)
        command = run.call_args.args[0]
        self.assertEqual(command[1:4], ["-m", "ainiee_cli", "queue"])
        self.assertEqual(
            command[command.index("--queue-file") + 1],
            "H:/Queue Files/tasks.json",
        )
        self.assertIn("--yes", command)

    def test_queue_skill_uses_custom_queue_file_for_management_actions(self):
        self._import_with_rapidjson("ModuleFolders.Service.TaskQueue.QueueManager")
        queue_module = self._import_with_rapidjson("Tools.Skills.skills.queue_skill")

        with tempfile.TemporaryDirectory() as temp_dir:
            queue_file = os.path.join(temp_dir, "custom-queue.json")
            added = queue_module.QueueSkill().execute(
                {
                    "action": "add",
                    "queue_file": queue_file,
                    "task_type": "translate",
                    "input_path": "input.txt",
                }
            )
            listed = queue_module.QueueSkill().execute(
                {"action": "list", "queue_file": queue_file}
            )
            cleared = queue_module.QueueSkill().execute(
                {"action": "clear", "queue_file": queue_file}
            )

        self.assertTrue(added.success)
        self.assertEqual(added.data["queue_file"], queue_file)
        self.assertTrue(listed.success)
        self.assertEqual(listed.data["count"], 1)
        self.assertTrue(cleared.success)

    def test_queue_skill_rejects_ephemeral_api_key(self):
        self._import_with_rapidjson("ModuleFolders.Service.TaskQueue.QueueManager")
        queue_module = self._import_with_rapidjson("Tools.Skills.skills.queue_skill")

        result = queue_module.QueueSkill().execute(
            {
                "action": "add",
                "task_type": "translate",
                "input_path": "input.txt",
                "api_key": "temporary-secret",
            }
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "INVALID_TASK")
        self.assertIn("cannot be stored", result.error)

    def test_legacy_boolean_values_are_normalized(self):
        spec = TaskSpec.from_mapping(
            {
                "task": "translate",
                "input_path": "input.txt",
                "resume": "false",
                "failover": "off",
            }
        )
        self.assertFalse(spec.resume)
        self.assertFalse(spec.failover)

    def test_skill_legacy_task_alias_remains_strict(self):
        common = self._import_with_rapidjson("Tools.Skills.skills.common")
        task_spec_from_skill_args = common.task_spec_from_skill_args

        spec = task_spec_from_skill_args(
            {"action": "run", "task": "polish", "input_path": "input.txt"}
        )
        self.assertEqual(spec.task_type, POLISH)
        with self.assertRaises(TaskContractError):
            task_spec_from_skill_args(
                {
                    "action": "run",
                    "task_type": "translate",
                    "input_path": "input.txt",
                    "typo_option": True,
                }
            )

    def test_skill_metadata_does_not_require_input_for_non_run_actions(self):
        self._import_with_rapidjson("ModuleFolders.Service.TaskQueue.QueueManager")
        queue_module = self._import_with_rapidjson("Tools.Skills.skills.queue_skill")
        translate_module = self._import_with_rapidjson(
            "Tools.Skills.skills.translate_skill"
        )

        queue_params = {param.name: param for param in queue_module.QueueSkill().meta.parameters}
        translate_params = {
            param.name: param for param in translate_module.TranslateSkill().meta.parameters
        }
        self.assertFalse(queue_params["input_path"].required)
        self.assertFalse(translate_params["input_path"].required)
        self.assertNotIn("manga", queue_params)
        self.assertIn("manga", translate_params)

    def test_queue_item_normalizes_legacy_task_type_and_boolean_values(self):
        queue_module = self._import_with_rapidjson(
            "ModuleFolders.Service.TaskQueue.QueueManager"
        )
        QueueTaskItem = queue_module.QueueTaskItem

        item = QueueTaskItem(
            "translation",
            "input.txt",
            resume="false",
            failover="off",
        )
        self.assertEqual(item.task_type, 1000)
        self.assertFalse(item.resume)
        self.assertFalse(item.failover)

        polish_item = QueueTaskItem(2, "input.txt")
        self.assertEqual(polish_item.task_type, 2000)

    def test_invalid_legacy_queue_item_is_marked_error(self):
        queue_module = self._import_with_rapidjson(
            "ModuleFolders.Service.TaskQueue.QueueManager"
        )
        QueueTaskItem = queue_module.QueueTaskItem

        item = QueueTaskItem.from_dict(
            {
                "task_type": "invalid",
                "input_path": "input.txt",
                "status": "waiting",
            }
        )
        self.assertEqual(item.status, "error")
        self.assertIn("task_contract_error", item.extra)

    def test_malformed_queue_rows_do_not_discard_valid_rows(self):
        queue_module = self._import_with_rapidjson(
            "ModuleFolders.Service.TaskQueue.QueueManager"
        )
        QueueManager = queue_module.QueueManager

        manager = object.__new__(QueueManager)
        manager.tasks = []
        data = [
            {"task_type": "translate", "input_path": "valid.txt"},
            None,
            {"task_type": "translate", "unexpected": True},
        ]
        manager._replace_tasks_from_data(data)

        self.assertEqual(len(manager.tasks), 3)
        self.assertEqual(manager.tasks[0].status, "waiting")
        self.assertEqual(manager.tasks[1].status, "error")
        self.assertEqual(manager.tasks[2].status, "error")
        with self.assertRaises(TaskContractError):
            manager.tasks[2].to_task_spec()

    def test_queue_stop_is_not_reported_as_completed(self):
        task_type_module = self._import_with_rapidjson(
            "ModuleFolders.Infrastructure.TaskConfig.TaskType"
        )
        TaskType = task_type_module.TaskType
        queue_module = self._import_with_rapidjson(
            "ModuleFolders.Service.TaskQueue.QueueManager"
        )
        QueueManager = queue_module.QueueManager
        QueueTaskItem = queue_module.QueueTaskItem
        Base = queue_module.Base

        manager = object.__new__(QueueManager)
        manager.tasks = [QueueTaskItem(TaskType.TRANSLATION, "input.txt")]
        manager.is_running = True
        manager._automation_stop_requested = False
        manager.current_task_index = -1
        manager.info = lambda *_args, **_kwargs: None
        manager._collect_completed_automation_outputs = lambda _task: None
        manager.hot_reload_queue = lambda *_args, **_kwargs: True
        manager.cleanup_stale_locks = lambda: None
        manager._process_workflow_tasks = lambda _cli_menu: None
        manager.save_tasks = lambda: None
        manager.start_task_processing = lambda index: (
            setattr(manager.tasks[index], "locked", True) or True
        )
        manager.stop_task_processing = lambda index: (
            setattr(manager.tasks[index], "locked", False) or True
        )

        def run_step(*_args, **_kwargs):
            Base.work_status = Base.STATUS.STOPING
            return False

        manager._run_single_step = run_step
        previous_work_status = getattr(Base, "work_status", None)
        Base.work_status = Base.STATUS.IDLE
        try:
            manager._process_queue(object())
        finally:
            Base.work_status = previous_work_status

        self.assertEqual(manager.tasks[0].status, "stopped")

    def test_workflow_step_overrides_and_string_false_are_normalized(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            workflow_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WorkflowRunner",
                fromlist=["WorkflowRunner"],
            )
        WorkflowRunner = workflow_module.WorkflowRunner

        class Host:
            active_profile_name = "default"
            active_rules_profile_name = "default"
            root_config = {}
            config = {
                "platforms": {"step-platform": {}},
                "label_output_path": "",
                "auto_set_output_path": True,
            }
            runtime_config_overrides = {}

            def load_config(self, active_profile_name=None, active_rules_profile_name=None):
                self.active_profile_name = active_profile_name or self.active_profile_name
                self.active_rules_profile_name = active_rules_profile_name or self.active_rules_profile_name

            def run_task(self, _task_type, **kwargs):
                self.captured = {
                    "kwargs": kwargs,
                    "config": dict(self.config),
                    "runtime": dict(self.runtime_config_overrides),
                }
                return True

        host = Host()
        runner = WorkflowRunner(host)
        with tempfile.NamedTemporaryFile() as input_file:
            runner._run_task_step(
                1000,
                input_file.name,
                {
                    "type": "translate",
                    "resume": "false",
                    "platform": "step-platform",
                    "threads": 0,
                    "pre_lines": 0,
                    "failover": "off",
                    "think_depth": "MAX",
                },
                {"task_type": "translate", "threads": 8, "resume": True},
            )

        self.assertFalse(host.captured["kwargs"]["continue_status"])
        self.assertEqual(host.captured["config"]["target_platform"], "step-platform")
        self.assertEqual(host.captured["config"]["user_thread_counts"], 0)
        self.assertEqual(host.captured["config"]["pre_line_counts"], 0)
        self.assertFalse(host.captured["config"]["enable_api_failover"])
        self.assertEqual(host.captured["config"]["think_depth"], "max")

    def test_workflow_accepts_legacy_task_alias_and_rejects_unknown_fields(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            workflow_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WorkflowRunner",
                fromlist=["WorkflowRunner"],
            )
        normalize = workflow_module.WorkflowRunner._normalize_task_config

        normalized = normalize({"task": "polish", "input_path": "input.txt"})
        self.assertEqual(normalized["task_type"], POLISH)
        with self.assertRaises(TaskContractError):
            normalize(
                {
                    "task_type": "translate",
                    "input_path": "input.txt",
                    "threds": 8,
                }
            )

    def test_workflow_steps_reject_unknown_types_and_fields(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            workflow_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WorkflowRunner",
                fromlist=["normalize_workflow_steps"],
            )
        normalize_steps = workflow_module.normalize_workflow_steps

        with self.assertRaises(TaskContractError):
            normalize_steps([{"type": "unknown"}])
        with self.assertRaises(TaskContractError):
            normalize_steps([{"type": "translate", "threds": 8}])
        with self.assertRaises(TaskContractError):
            normalize_steps([{"type": "extract_glossary", "min_frequncy": 2}])

        normalized = normalize_steps(
            [
                {
                    "type": "translate",
                    "threads": "4",
                    "think_depth": "HIGH",
                    "output_root": "output",
                }
            ]
        )
        self.assertEqual(normalized[0]["threads"], "4")
        self.assertEqual(normalized[0]["think_depth"], "HIGH")

    def test_workflow_rejects_unknown_step_fields_and_types(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            workflow_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WorkflowRunner",
                fromlist=["normalize_workflow_steps"],
            )

        with self.assertRaises(TaskContractError):
            workflow_module.normalize_workflow_steps(
                [{"type": "translate", "threds": 8}], "translate"
            )
        with self.assertRaises(TaskContractError):
            workflow_module.normalize_workflow_steps(
                [{"type": "transalte"}], "translate"
            )

    def test_automation_managers_forward_legacy_limit_aliases(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            scheduler_module = __import__(
                "ModuleFolders.Infrastructure.Automation.SchedulerManager",
                fromlist=["ScheduledTask", "SchedulerManager"],
            )
            watch_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WatchManager",
                fromlist=["WatchRule", "WatchManager"],
            )

        scheduled = scheduler_module.ScheduledTask(
            "schedule-1", "unit", "0 0 * * *", "input.txt", lines=12
        )
        scheduled_payload = {}
        scheduler = scheduler_module.SchedulerManager(
            execute_callback=lambda payload: scheduled_payload.update(payload)
        )
        executed = threading.Event()
        scheduler._run_task_thread = lambda _task, payload: (
            scheduled_payload.update(payload), executed.set()
        )
        scheduler._execute_task(scheduled)
        self.assertTrue(executed.wait(1))
        self.assertEqual(scheduled_payload["lines"], 12)

        with tempfile.TemporaryDirectory() as watch_dir:
            watch = watch_module.WatchRule(
                "watch-1", watch_dir, auto_start=False, tokens=1000
            )
            watch_payload = {}
            manager = watch_module.WatchManager(
                queue_callback=lambda payload: watch_payload.update(payload)
            )
            with tempfile.NamedTemporaryFile() as input_file:
                manager._process_single_target(input_file.name, watch, skip_stability=True)
        self.assertEqual(watch_payload["tokens"], 1000)

    def test_automation_rule_serialization_removes_credentials(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            watch_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WatchManager",
                fromlist=["WatchRule"],
            )
            scheduler_module = __import__(
                "ModuleFolders.Infrastructure.Automation.SchedulerManager",
                fromlist=["ScheduledTask"],
            )

        watch = watch_module.WatchRule(
            "watch-1",
            ".",
            api_key="watch-secret",
            extra={"nested": {"client_secret": "nested-secret", "keep": True}},
        )
        scheduled = scheduler_module.ScheduledTask(
            "schedule-1",
            "unit",
            "0 0 * * *",
            "input.txt",
            api_key="schedule-secret",
            extra={"nested": {"auth_token": "nested-secret", "keep": True}},
        )

        watch_data = watch.to_dict()
        scheduled_data = scheduled.to_dict()
        self.assertNotIn("api_key", watch_data)
        self.assertNotIn("api_key", scheduled_data)
        self.assertEqual(watch_data["nested"], {"keep": True})
        self.assertEqual(scheduled_data["nested"], {"keep": True})

    def test_automation_rules_validate_override_fields_and_reject_typos(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            watch_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WatchManager",
                fromlist=["WatchRule"],
            )
            scheduler_module = __import__(
                "ModuleFolders.Infrastructure.Automation.SchedulerManager",
                fromlist=["ScheduledTask"],
            )

        watch = watch_module.WatchRule(
            "watch-1",
            ".",
            threads="4",
            think_depth="HIGH",
            lines_limit=20,
        )
        scheduled = scheduler_module.ScheduledTask(
            "schedule-1",
            "unit",
            "0 0 * * *",
            "input.txt",
            failover="off",
            thinking_budget="4096",
            tokens_limit=2048,
        )

        self.assertEqual(watch.extra["threads"], 4)
        self.assertEqual(watch.extra["think_depth"], "high")
        self.assertEqual(watch.task_type, TRANSLATE)
        self.assertEqual(scheduled.extra["failover"], False)
        self.assertEqual(scheduled.extra["thinking_budget"], 4096)
        self.assertEqual(scheduled.task_type, TRANSLATE)
        with self.assertRaises(TaskContractError):
            watch_module.WatchRule("watch-typo", ".", threds=8)
        with self.assertRaises(TaskContractError):
            scheduler_module.ScheduledTask(
                "schedule-typo",
                "unit",
                "0 0 * * *",
                "input.txt",
                threds=8,
            )

    def test_automation_rule_from_dict_rejects_unknown_fields(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            watch_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WatchManager",
                fromlist=["WatchRule"],
            )
            scheduler_module = __import__(
                "ModuleFolders.Infrastructure.Automation.SchedulerManager",
                fromlist=["ScheduledTask"],
            )

        with self.assertRaises(TaskContractError):
            watch_module.WatchRule.from_dict(
                {"id": "watch-typo", "watch_path": ".", "threds": 8}
            )
        with self.assertRaises(TaskContractError):
            scheduler_module.ScheduledTask.from_dict(
                {
                    "id": "schedule-typo",
                    "name": "unit",
                    "schedule": "0 0 * * *",
                    "input_path": "input.txt",
                    "threds": 8,
                }
            )

    def test_automation_rule_contract_overrides_survive_round_trip(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            watch_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WatchManager",
                fromlist=["WatchRule"],
            )
            scheduler_module = __import__(
                "ModuleFolders.Infrastructure.Automation.SchedulerManager",
                fromlist=["ScheduledTask"],
            )

        watch = watch_module.WatchRule(
            "watch-round-trip",
            ".",
            task_type="translation",
            threads="4",
            lines=12,
            think_depth="HIGH",
        )
        scheduled = scheduler_module.ScheduledTask(
            "schedule-round-trip",
            "unit",
            "0 0 * * *",
            "input.txt",
            task_type="polishing",
            tokens=2048,
            failover="off",
        )

        restored_watch = watch_module.WatchRule.from_dict(watch.to_dict())
        restored_scheduled = scheduler_module.ScheduledTask.from_dict(
            scheduled.to_dict()
        )

        self.assertEqual(restored_watch.task_type, TRANSLATE)
        self.assertEqual(restored_watch.extra["threads"], 4)
        self.assertEqual(restored_watch.extra["lines"], 12)
        self.assertEqual(restored_watch.extra["think_depth"], "high")
        self.assertEqual(restored_scheduled.task_type, POLISH)
        self.assertEqual(restored_scheduled.extra["tokens"], 2048)
        self.assertFalse(restored_scheduled.extra["failover"])

    def test_automation_manager_updates_override_fields_and_rejects_typos(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            watch_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WatchManager",
                fromlist=["WatchManager", "WatchRule"],
            )
            scheduler_module = __import__(
                "ModuleFolders.Infrastructure.Automation.SchedulerManager",
                fromlist=["SchedulerManager", "ScheduledTask"],
            )

        watch_manager = watch_module.WatchManager()
        watch = watch_module.WatchRule("watch-1", ".")
        watch_manager.rules[watch.id] = watch
        self.assertTrue(watch_manager.update_rule(watch.id, threads="6"))
        self.assertEqual(watch.extra["threads"], 6)
        self.assertTrue(watch_manager.update_rule(watch.id, task_type="polishing"))
        self.assertEqual(watch.task_type, POLISH)
        with self.assertRaises(TaskContractError):
            watch_manager.update_rule(watch.id, threds=8)

        scheduler_manager = scheduler_module.SchedulerManager()
        scheduled = scheduler_module.ScheduledTask(
            "schedule-1", "unit", "0 0 * * *", "input.txt"
        )
        scheduler_manager.tasks[scheduled.id] = scheduled
        self.assertTrue(
            scheduler_manager.update_task(scheduled.id, think_depth="HIGH")
        )
        self.assertEqual(scheduled.extra["think_depth"], "high")
        self.assertTrue(
            scheduler_manager.update_task(scheduled.id, task_type="polishing")
        )
        self.assertEqual(scheduled.task_type, POLISH)
        with self.assertRaises(TaskContractError):
            scheduler_manager.update_task(scheduled.id, threds=8)

    def test_workflow_polish_defaults_to_resume_but_honors_explicit_false(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            workflow_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WorkflowRunner",
                fromlist=["WorkflowRunner"],
            )
        WorkflowRunner = workflow_module.WorkflowRunner

        class Host:
            active_profile_name = "default"
            active_rules_profile_name = "default"
            root_config = {}
            config = {}
            runtime_config_overrides = {}

            def load_config(self, **_kwargs):
                return None

        host = Host()
        runner = WorkflowRunner(host)
        resume_values = []
        runner._run_task_step = lambda *_args, resume=False, **_kwargs: resume_values.append(resume)

        runner.run({"task_type": "polish", "input_path": "input.txt"})
        runner.run(
            {"task_type": "polish", "input_path": "input.txt", "resume": "false"}
        )

        self.assertEqual(resume_values, [True, False])

    def test_automation_queue_preserves_resume_presence(self):
        queue_module = self._import_with_rapidjson(
            "ModuleFolders.Service.TaskQueue.QueueManager"
        )
        QueueTaskItem = queue_module.QueueTaskItem

        implicit = QueueTaskItem(
            "polish",
            "input.txt",
            resume=False,
            resume_explicit=False,
            workflow_steps=[{"type": "polish"}],
        )
        explicit = QueueTaskItem(
            "polish",
            "input.txt",
            resume=False,
            resume_explicit=True,
            workflow_steps=[{"type": "polish"}],
        )

        self.assertNotIn("resume", implicit.to_runtime_dict())
        self.assertFalse(explicit.to_runtime_dict()["resume"])
        self.assertFalse(implicit.to_persistent_dict()["resume_explicit"])
        self.assertTrue(explicit.to_persistent_dict()["resume_explicit"])

        restored_implicit = QueueTaskItem.from_dict(
            {
                "task_type": "polish",
                "input_path": "input.txt",
                "workflow_steps": [{"type": "polish"}],
            }
        )
        restored_explicit = QueueTaskItem.from_dict(
            {
                "task_type": "polish",
                "input_path": "input.txt",
                "resume": False,
                "workflow_steps": [{"type": "polish"}],
            }
        )
        restored_null = QueueTaskItem.from_dict(
            {
                "task_type": "polish",
                "input_path": "input.txt",
                "resume": None,
                "workflow_steps": [{"type": "polish"}],
            }
        )
        self.assertNotIn("resume", restored_implicit.to_runtime_dict())
        self.assertFalse(restored_explicit.to_runtime_dict()["resume"])
        self.assertFalse(restored_null.resume_explicit)
        self.assertNotIn("resume", restored_null.to_runtime_dict())

        legacy_workflow = QueueTaskItem(
            "polish",
            "input.txt",
            workflow_steps=[{"type": "polish"}],
        )
        self.assertIsNone(legacy_workflow.resume_explicit)
        self.assertNotIn("resume", legacy_workflow.to_runtime_dict())

    def test_workflow_all_in_one_resume_override_applies_to_both_phases(self):
        natsort = SimpleNamespace(
            natsorted=sorted,
            natsort_keygen=lambda *_args, **_kwargs: (lambda value: value),
            ns=SimpleNamespace(PATH=1, IGNORECASE=2),
        )
        with patch.dict(sys.modules, {"rapidjson": json, "natsort": natsort}):
            workflow_module = __import__(
                "ModuleFolders.Infrastructure.Automation.WorkflowRunner",
                fromlist=["WorkflowRunner"],
            )
        WorkflowRunner = workflow_module.WorkflowRunner
        Base = workflow_module.Base

        runner = WorkflowRunner(SimpleNamespace())
        resume_values = []
        runner._run_task_step = lambda *_args, resume=False, **_kwargs: resume_values.append(resume)
        had_work_status = hasattr(Base, "work_status")
        previous_work_status = getattr(Base, "work_status", None)
        Base.work_status = Base.STATUS.IDLE
        try:
            runner._run_all_in_one_step(
                "input.txt",
                {"type": "all_in_one"},
                {"task_type": "all_in_one"},
            )
            runner._run_all_in_one_step(
                "input.txt",
                {"type": "all_in_one"},
                {"task_type": "all_in_one", "resume": False},
            )
        finally:
            if had_work_status:
                Base.work_status = previous_work_status
            else:
                del Base.work_status

        self.assertEqual(resume_values, [False, True, False, False])

    def test_queue_polish_only_moves_to_polish_phase(self):
        task_type_module = self._import_with_rapidjson(
            "ModuleFolders.Infrastructure.TaskConfig.TaskType"
        )
        TaskType = task_type_module.TaskType
        queue_module = self._import_with_rapidjson(
            "ModuleFolders.Service.TaskQueue.QueueManager"
        )
        QueueManager = queue_module.QueueManager
        QueueTaskItem = queue_module.QueueTaskItem
        Base = queue_module.Base

        manager = object.__new__(QueueManager)
        manager.tasks = [QueueTaskItem(TaskType.POLISH, "input.txt")]
        manager.is_running = True
        manager._automation_stop_requested = False
        manager.current_task_index = -1
        manager.info = lambda *_args, **_kwargs: None
        manager._collect_completed_automation_outputs = lambda _task: None
        manager.hot_reload_queue = lambda *_args, **_kwargs: True
        manager.cleanup_stale_locks = lambda: None
        manager._process_workflow_tasks = lambda _cli_menu: None
        manager.save_tasks = lambda: None
        manager.start_task_processing = lambda _index: True
        manager.stop_task_processing = lambda _index: True
        calls = []

        def run_step(_cli_menu, task, step_type, resume=False):
            calls.append((step_type, resume))
            return True

        manager._run_single_step = run_step
        had_work_status = hasattr(Base, "work_status")
        previous_work_status = getattr(Base, "work_status", None)
        Base.work_status = Base.STATUS.IDLE
        try:
            manager._process_queue(object())
        finally:
            if had_work_status:
                Base.work_status = previous_work_status
            else:
                del Base.work_status

        self.assertEqual(calls, [(TaskType.POLISH, True)])
        self.assertEqual(manager.tasks[0].status, "completed")


if __name__ == "__main__":
    unittest.main()
