import os
import subprocess
import sys
import threading
from typing import Dict

import rapidjson as json

from ModuleFolders.Infrastructure.Automation.AutomationProgress import (
    PROGRESS_FILE_ENV,
    RUN_ID_ENV,
    TASK_SECRETS_ENV,
    TERMINAL_STATUSES,
    AutomationProgressReporter,
    get_progress_dir,
    new_run_id,
    progress_file_for_run,
    read_progress_file,
    scrub_legacy_task_credentials,
    task_config_file_for_run,
)
from ModuleFolders.Infrastructure.SensitiveData import split_sensitive_data
from ModuleFolders.Infrastructure.Automation.WorkflowRunner import describe_workflow_steps, WorkflowRunner


class AutomationProcessRunner:
    _lock = threading.RLock()
    _processes: Dict[str, subprocess.Popen] = {}
    _progress_files: Dict[str, str] = {}
    _task_config_files: Dict[str, str] = {}

    @classmethod
    def start(cls, task_config: dict, project_root: str = None) -> dict:
        project_root = project_root or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        scrub_legacy_task_credentials(get_progress_dir(project_root))
        run_id = new_run_id(task_config.get("rule_id", ""), task_config.get("input_path", ""))
        progress_file = progress_file_for_run(run_id, project_root)
        task_config_path = task_config_file_for_run(run_id, project_root)

        runtime_config = dict(task_config)
        runtime_config["automation_run_id"] = run_id
        runtime_config["automation_progress_file"] = progress_file
        runtime_config["workflow_description"] = describe_workflow_steps(runtime_config.get("workflow_steps") or [])
        prepared_config, secret_overlay = split_sensitive_data(runtime_config)

        with open(task_config_path, "w", encoding="utf-8") as file:
            json.dump(prepared_config, file, ensure_ascii=False, indent=2)

        reporter = AutomationProgressReporter(
            progress_file,
            run_id,
            initial={
                "status": "queued",
                "phase": "queued",
                "message": "Automation task queued",
                "input_path": prepared_config.get("input_path", ""),
                "file_name": (
                    prepared_config.get("trigger_file_name")
                    or os.path.basename(os.path.normpath(prepared_config.get("trigger_file_path") or ""))
                    or os.path.basename(os.path.normpath(prepared_config.get("input_path", "")))
                ),
                "rule_id": prepared_config.get("rule_id", ""),
                "workflow": prepared_config.get("workflow_description", ""),
            },
        )

        env = os.environ.copy()
        env.pop(TASK_SECRETS_ENV, None)
        env[PROGRESS_FILE_ENV] = progress_file
        env[RUN_ID_ENV] = run_id
        if secret_overlay:
            # Each Popen receives its own environment copy, so concurrent workflow
            # credentials cannot overwrite one another or reach the task file.
            env[TASK_SECRETS_ENV] = json.dumps(secret_overlay, ensure_ascii=False)

        log_path = os.path.join(os.path.dirname(progress_file), f"{run_id}.worker.log")
        log_file = open(log_path, "a", encoding="utf-8")
        cmd = [
            sys.executable,
            "-m",
            "ModuleFolders.Infrastructure.Automation.AutomationWorker",
            "--task-config",
            task_config_path,
        ]
        process = subprocess.Popen(
            cmd,
            cwd=project_root,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        reporter.update(status="running", phase="worker", pid=process.pid, message="Automation worker started")

        with cls._lock:
            cls._processes[run_id] = process
            cls._progress_files[run_id] = progress_file
            cls._task_config_files[run_id] = task_config_path

        threading.Thread(
            target=cls._watch_process,
            args=(run_id, process, log_file, progress_file),
            daemon=True,
        ).start()

        return {
            "run_id": run_id,
            "progress_file": progress_file,
            "task_config_file": task_config_path,
            "pid": process.pid,
        }

    @classmethod
    def _watch_process(cls, run_id: str, process: subprocess.Popen, log_file, progress_file: str):
        return_code = process.wait()
        try:
            log_file.close()
        except Exception:
            pass

        state = read_progress_file(progress_file)
        if state.get("status") not in TERMINAL_STATUSES:
            reporter = AutomationProgressReporter(
                progress_file,
                run_id,
                initial=cls._state_for_resume(state),
                emit_initial=False,
            )
            if return_code == 0:
                task_config = cls._read_task_config_for_run(run_id, progress_file)
                if WorkflowRunner.is_enqueue_only(task_config):
                    reporter.finish("enqueued", "Queued for task processing")
                else:
                    reporter.finish("completed", "Automation worker completed")
            else:
                reporter.finish("interrupted", f"Automation worker exited with code {return_code}")

        with cls._lock:
            cls._processes.pop(run_id, None)
            cls._progress_files.pop(run_id, None)
            cls._task_config_files.pop(run_id, None)

    @classmethod
    def _read_task_config_for_run(cls, run_id: str, progress_file: str) -> dict:
        with cls._lock:
            task_config_path = cls._task_config_files.get(run_id)
        if not task_config_path:
            task_config_path = os.path.splitext(progress_file)[0] + ".task.json"
        try:
            with open(task_config_path, "r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _state_for_resume(state: dict) -> dict:
        return {
            key: value
            for key, value in (state or {}).items()
            if not str(key).startswith("_") and key != "logs"
        }

    @classmethod
    def get_process(cls, run_id: str):
        with cls._lock:
            return cls._processes.get(run_id)

    @classmethod
    def snapshot_processes(cls) -> dict:
        with cls._lock:
            return dict(cls._processes)

    @classmethod
    def terminate(cls, run_id: str, message: str = "Automation task interrupted") -> bool:
        with cls._lock:
            process = cls._processes.get(run_id)
            progress_file = cls._progress_files.get(run_id) or progress_file_for_run(run_id)
        if not process:
            return False

        if process.poll() is None:
            process.terminate()
        state = read_progress_file(progress_file)
        reporter = AutomationProgressReporter(
            progress_file,
            run_id,
            initial=cls._state_for_resume(state),
            emit_initial=False,
        )
        reporter.finish("interrupted", message)
        return True

    @classmethod
    def terminate_all(cls, message: str = "Automation status view exited; background tasks interrupted") -> int:
        with cls._lock:
            run_ids = list(cls._processes.keys())
        count = 0
        for run_id in run_ids:
            if cls.terminate(run_id, message):
                count += 1
        return count
