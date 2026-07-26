import argparse
import os
import sys
import traceback

import rapidjson as json

from ModuleFolders.Base.Base import Base, TUIHandler
from ModuleFolders.Base.EventManager import EventManager
from ModuleFolders.Infrastructure.Automation.AutomationProgress import (
    AutomationProgressUI,
    TASK_SECRETS_ENV,
    TERMINAL_STATUSES,
    read_progress_file,
    reporter_from_env,
)
from ModuleFolders.Infrastructure.SensitiveData import restore_sensitive_data


def _load_task_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        task_config = json.load(file)

    # Credentials arrive through the child-only environment and are removed as
    # soon as the in-memory task has been reconstructed.
    raw_secrets = os.environ.pop(TASK_SECRETS_ENV, "")
    if raw_secrets:
        secret_overlay = json.loads(raw_secrets)
        task_config = restore_sensitive_data(task_config, secret_overlay)
    return task_config


def run_worker(task_config_path: str) -> int:
    task_config = _load_task_config(task_config_path)
    reporter = reporter_from_env(
        {
            "rule_id": task_config.get("rule_id", ""),
            "input_path": task_config.get("input_path", ""),
            "file_name": (
                task_config.get("trigger_file_name")
                or os.path.basename(os.path.normpath(task_config.get("trigger_file_path") or ""))
                or os.path.basename(os.path.normpath(task_config.get("input_path", "")))
            ),
            "workflow": task_config.get("workflow_description", ""),
            "status": "starting",
            "phase": "worker",
            "message": "Automation worker starting",
        }
    )

    try:
        import ainiee_cli
        from ModuleFolders.Infrastructure.Automation.WorkflowRunner import AutomationPartialCompletion, WorkflowRunner

        cli = ainiee_cli.CLIMenu()
        ui = AutomationProgressUI(reporter) if reporter else None
        if ui:
            cli.ui = ui
            TUIHandler.set_ui(ui)
            Base.print = ui.log
            EventManager.get_singleton().subscribe(Base.EVENT.TASK_UPDATE, ui.update_progress)
            EventManager.get_singleton().subscribe(Base.EVENT.SYSTEM_STATUS_UPDATE, ui.update_status)

        if reporter:
            reporter.update(status="workflow", phase="workflow", message="Workflow started")

        runner = WorkflowRunner(cli, progress_reporter=reporter)
        runner.run(task_config)

        if ui:
            state = read_progress_file(reporter.progress_file) if reporter else {}
            if state.get("status") not in TERMINAL_STATUSES:
                if runner.is_enqueue_only(task_config):
                    ui.finish("enqueued", "Queued for task processing")
                else:
                    ui.finish("completed", "Workflow completed")
        return 0
    except AutomationPartialCompletion as exc:
        if reporter:
            reporter.finish("partial", str(exc))
        return 0
    except Exception as exc:
        if reporter:
            reporter.log(traceback.format_exc(), level="error")
            reporter.finish("error", str(exc))
        return 1
    finally:
        if "ui" in locals() and ui:
            EventManager.get_singleton().unsubscribe(Base.EVENT.TASK_UPDATE, ui.update_progress)
            EventManager.get_singleton().unsubscribe(Base.EVENT.SYSTEM_STATUS_UPDATE, ui.update_status)
        TUIHandler.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description="AiNiee automation worker")
    parser.add_argument("--task-config", required=True)
    args = parser.parse_args()
    return run_worker(args.task_config)


if __name__ == "__main__":
    sys.exit(main())
