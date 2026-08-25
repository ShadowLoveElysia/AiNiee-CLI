"""
定时任务调度管理器
支持 cron 表达式和简单时间规则
"""
import os
import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Any
import rapidjson as json
from ModuleFolders.Base.Base import Base
from ModuleFolders.Infrastructure.Automation.WorkflowRunner import normalize_workflow_steps
from ModuleFolders.Infrastructure.SensitiveData import sanitize_sensitive_data
from ModuleFolders.Infrastructure.TaskContract import (
    QUEUE_TASK_TYPES,
    QUEUE_TASK_OVERRIDE_FIELDS,
    TaskContractError,
    TaskSpec,
    select_task_contract_fields,
)


SCHEDULED_TASK_OVERRIDE_FIELDS = (
    frozenset(QUEUE_TASK_OVERRIDE_FIELDS) | {"lines", "tokens"}
) - {
    "input_path",
    "output_path",
    "profile",
    "rules_profile",
}
SCHEDULED_TASK_RUNTIME_METADATA_FIELDS = frozenset(
    {
        "last_run",
        "next_run",
        "run_count",
        "last_status",
        "pending_event",
        "pending_event_count",
    }
)


class CronParser:
    """简单的 Cron 表达式解析器"""

    @staticmethod
    def parse(cron_expr: str) -> dict:
        """
        解析 cron 表达式: 分 时 日 月 周
        支持: *, */n, n, n-m, n,m,o
        """
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expr}")

        return {
            "minute": CronParser._parse_field(parts[0], 0, 59),
            "hour": CronParser._parse_field(parts[1], 0, 23),
            "day": CronParser._parse_field(parts[2], 1, 31),
            "month": CronParser._parse_field(parts[3], 1, 12),
            "weekday": CronParser._parse_field(parts[4], 0, 6),  # 0=周日
        }

    @staticmethod
    def _parse_field(field: str, min_val: int, max_val: int) -> set:
        """解析单个字段"""
        result = set()

        for part in field.split(","):
            part = part.strip()
            if not part:
                raise ValueError("Empty cron field")
            if part == "*":
                result.update(range(min_val, max_val + 1))
            elif part.startswith("*/"):
                step = int(part[2:])
                if step <= 0:
                    raise ValueError("Cron step must be greater than zero")
                result.update(range(min_val, max_val + 1, step))
            elif "-" in part:
                start, end = map(int, part.split("-"))
                if start > end:
                    raise ValueError("Cron range start must be <= end")
                result.update(range(start, end + 1))
            else:
                result.add(int(part))

        if not result or min(result) < min_val or max(result) > max_val:
            raise ValueError(f"Cron field out of range: {field}")
        return result

    @staticmethod
    def matches(cron_dict: dict, dt: datetime) -> bool:
        """检查时间是否匹配 cron 规则"""
        cron_weekday = (dt.weekday() + 1) % 7
        return (
            dt.minute in cron_dict["minute"] and
            dt.hour in cron_dict["hour"] and
            dt.day in cron_dict["day"] and
            dt.month in cron_dict["month"] and
            cron_weekday in cron_dict["weekday"]
        )


class ScheduleParser:
    """Parse cron expressions and user-friendly time expressions."""

    DATE_RE = re.compile(r"^(?:(?P<year>\d{4})[/-])?(?P<month>\d{1,2})[/-](?P<day>\d{1,2})$")
    TIME_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")

    @staticmethod
    def parse(expression: str, *, allow_empty: bool = False) -> dict:
        text = str(expression or "").strip()
        if not text:
            if allow_empty:
                return {"type": "none"}
            raise ValueError("Schedule expression is required")

        parts = text.split()
        if len(parts) == 5:
            return {"type": "cron", "cron": CronParser.parse(text), "source": text}

        dates = []
        times = []
        for token in ScheduleParser._split_simple_tokens(text):
            date_match = ScheduleParser.DATE_RE.match(token)
            if date_match:
                year = date_match.group("year")
                month = int(date_match.group("month"))
                day = int(date_match.group("day"))
                if month < 1 or month > 12 or day < 1 or day > 31:
                    raise ValueError(f"Date out of range: {token}")
                dates.append({
                    "year": int(year) if year else None,
                    "month": month,
                    "day": day,
                })
                continue

            time_match = ScheduleParser.TIME_RE.match(token)
            if time_match:
                hour = int(time_match.group("hour"))
                minute = int(time_match.group("minute"))
                if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                    raise ValueError(f"Time out of range: {token}")
                times.append((hour, minute))
                continue

            raise ValueError(f"Invalid schedule token: {token}")

        if not dates and not times:
            raise ValueError("Schedule expression is required")
        if not times:
            times.append((0, 0))

        return {
            "type": "simple",
            "dates": dates,
            "times": sorted(set(times)),
            "source": text,
        }

    @staticmethod
    def _split_simple_tokens(text: str) -> List[str]:
        normalized = (
            text.replace("，", ",")
            .replace("、", ",")
            .replace("@", " ")
            .replace("T", " ")
        )
        return [part for part in re.split(r"[\s,]+", normalized) if part]

    @staticmethod
    def matches(schedule_rule: dict, dt: datetime) -> bool:
        rule_type = schedule_rule.get("type")
        if rule_type == "cron":
            return CronParser.matches(schedule_rule["cron"], dt)
        if rule_type == "simple":
            if (dt.hour, dt.minute) not in schedule_rule.get("times", []):
                return False
            dates = schedule_rule.get("dates") or []
            if not dates:
                return True
            for date_rule in dates:
                if (
                    date_rule.get("month") == dt.month
                    and date_rule.get("day") == dt.day
                    and (date_rule.get("year") in (None, dt.year))
                ):
                    return True
        return False

    @staticmethod
    def next_run(schedule_rule: dict, from_time: datetime = None) -> Optional[datetime]:
        if schedule_rule.get("type") == "none":
            return None

        now = from_time or datetime.now()
        check_time = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        max_minutes = 7 * 24 * 60 if schedule_rule.get("type") == "cron" else 370 * 24 * 60
        for _ in range(max_minutes):
            if ScheduleParser.matches(schedule_rule, check_time):
                return check_time
            check_time += timedelta(minutes=1)
        return None


class ScheduledTask:
    """定时任务"""

    def __init__(self, task_id: str, name: str, schedule: str,
                 input_path: str, profile: str = "default",
                 task_type: str = "translation", enabled: bool = True,
                 output_path: str = "", workflow_steps: List[dict] = None,
                 run_queue: bool = False, rules_profile: str = "", **kwargs):
        self.id = task_id
        self.name = name
        self.trigger_type = kwargs.pop("trigger_type", "scheduled") or "scheduled"
        self.event_type = kwargs.pop("event_type", "")
        metadata = kwargs.pop("extra", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise TaskContractError("Scheduled task extra metadata must be a mapping")
        unknown = sorted(
            set(kwargs)
            - SCHEDULED_TASK_OVERRIDE_FIELDS
            - SCHEDULED_TASK_RUNTIME_METADATA_FIELDS
        )
        if unknown:
            raise TaskContractError(f"Unknown scheduled task fields: {', '.join(unknown)}")
        if self.trigger_type == "queue_added":
            self.event_type = "queue_added"
        elif self.trigger_type == "queue_pending":
            self.event_type = "queue_pending"
            schedule = ""
        if self.trigger_type in {"queue_added", "queue_pending"}:
            input_path = input_path or "queue"
            run_queue = True
            workflow_steps = [{"type": "run_queue"}]
        overrides = {
            key: kwargs[key]
            for key in SCHEDULED_TASK_OVERRIDE_FIELDS
            if key in kwargs
        }
        if self.trigger_type not in {"queue_added", "queue_pending"}:
            payload = {
                "task_type": task_type,
                "input_path": input_path,
                "output_path": output_path or None,
                "profile": profile or None,
                "rules_profile": rules_profile or None,
                **overrides,
            }
            normalized = TaskSpec.from_mapping(payload).to_mapping(
                include_none=True,
                include_api_key=True,
            )
            if normalized["task_type"] not in QUEUE_TASK_TYPES or normalized["manga"]:
                raise TaskContractError(
                    f"Task type {normalized['task_type']!r} is not supported by scheduled tasks"
                )
            overrides = {
                key: (
                    normalized["lines_limit"]
                    if key == "lines"
                    else normalized["tokens_limit"]
                    if key == "tokens"
                    else normalized[key]
                )
                for key in SCHEDULED_TASK_OVERRIDE_FIELDS
                if key in overrides
            }
        self.schedule = schedule
        self.input_path = input_path
        self.output_path = output_path
        self.profile = profile
        self.rules_profile = rules_profile
        self.task_type = (
            task_type
            if self.trigger_type in {"queue_added", "queue_pending"}
            else normalized["task_type"]
        )
        self.enabled = enabled
        self.workflow_steps = (
            normalize_workflow_steps(workflow_steps, self.task_type, True)
            if workflow_steps
            else []
        )
        self.run_queue = run_queue
        self.extra = {**metadata, **overrides}

        # 解析时间表达式，兼容 cron 与用户友好的时间写法。
        self.schedule_rule = ScheduleParser.parse(
            schedule,
            allow_empty=self.trigger_type in {"queue_added", "queue_pending"},
        )
        if self.trigger_type == "scheduled" and self.schedule_rule.get("type") == "none":
            raise ValueError("Scheduled trigger requires a schedule expression")
        self.cron_dict = self.schedule_rule

        # 运行状态
        self.last_run: Optional[datetime] = None
        self.next_run: Optional[datetime] = None
        self.run_count = 0
        self.last_status = ""
        self.pending_event = False
        self.pending_event_count = 0

        self._calculate_next_run()

    def _calculate_next_run(self):
        """计算下次运行时间"""
        self.next_run = ScheduleParser.next_run(self.schedule_rule)

    def should_run(self, current_time: datetime) -> bool:
        """检查是否应该运行"""
        if not self.enabled:
            return False

        if self.trigger_type in {"queue_added", "queue_pending"}:
            if not self.pending_event:
                return False
            if self.schedule_rule.get("type") == "none":
                return True
            if not self.next_run:
                self._calculate_next_run()
            return bool(self.next_run and current_time >= self.next_run)

        if not self.next_run:
            return False

        # 检查是否到达运行时间（允许 1 分钟误差）
        if current_time >= self.next_run:
            return True

        return False

    def notify_queue_added(self, count: int = 1):
        """标记队列新增事件，供自定义触发规则使用。"""
        if self.pending_event and self.schedule_rule.get("type") == "none":
            return
        self.pending_event = True
        self.pending_event_count += max(1, int(count or 1))
        if self.schedule_rule.get("type") == "none":
            self.next_run = None
        else:
            self._calculate_next_run()

    def mark_run(self, status: str = "success"):
        """标记已运行"""
        self.last_run = datetime.now()
        self.last_status = status
        self.run_count += 1
        if self.trigger_type in {"queue_added", "queue_pending"}:
            self.pending_event = False
            self.pending_event_count = 0
        self._calculate_next_run()

    def to_dict(self) -> dict:
        """转换为字典"""
        return sanitize_sensitive_data({
            "id": self.id,
            "name": self.name,
            "schedule": self.schedule,
            "trigger_type": self.trigger_type,
            "event_type": self.event_type,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "profile": self.profile,
            "rules_profile": self.rules_profile,
            "task_type": self.task_type,
            "enabled": self.enabled,
            "workflow_steps": self.workflow_steps,
            "run_queue": self.run_queue,
            **self.extra
        })

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduledTask":
        """从字典创建"""
        known_fields = {
            "id", "name", "schedule", "trigger_type", "event_type",
            "input_path", "output_path", "profile", "rules_profile",
            "task_type", "enabled", "workflow_steps", "run_queue",
        }
        return cls(
            task_id=data.get("id", ""),
            name=data.get("name", ""),
            schedule=data.get("schedule", "0 0 * * *"),
            trigger_type=data.get("trigger_type", "scheduled"),
            event_type=data.get("event_type", ""),
            input_path=data.get("input_path", ""),
            output_path=data.get("output_path", ""),
            profile=data.get("profile", "default"),
            rules_profile=data.get("rules_profile", ""),
            task_type=data.get("task_type", "translation"),
            enabled=data.get("enabled", True),
            workflow_steps=data.get("workflow_steps"),
            run_queue=data.get("run_queue", False),
            **{key: value for key, value in data.items() if key not in known_fields},
        )


class SchedulerManager(Base):
    """定时任务调度管理器"""

    def __init__(self, execute_callback: Callable[[dict], Any] = None):
        super().__init__()
        self.execute_callback = execute_callback
        self.tasks: Dict[str, ScheduledTask] = {}
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._queue_signature = None
        self.event_triggers_active = False

        # 日志
        self.logs: List[dict] = []
        self.max_logs = 100

    def set_event_triggers_active(self, active: bool):
        """Enable event-based triggers for the automation status preview."""
        self.event_triggers_active = bool(active)
        if not active:
            self._queue_signature = None

    def set_callback(self, callback: Callable[[dict], Any]):
        """设置执行回调"""
        self.execute_callback = callback

    def add_task(self, task: ScheduledTask) -> bool:
        """添加定时任务"""
        with self._lock:
            if task.id in self.tasks:
                return False
            self.tasks[task.id] = task
            self._log("info", f"Task added: {task.name} ({task.schedule})")
            return True

    def remove_task(self, task_id: str) -> bool:
        """移除定时任务"""
        with self._lock:
            if task_id in self.tasks:
                task = self.tasks.pop(task_id)
                self._log("info", f"Task removed: {task.name}")
                return True
            return False

    def update_task(self, task_id: str, **kwargs) -> bool:
        """更新任务配置"""
        with self._lock:
            if task_id not in self.tasks:
                return False

            task = self.tasks[task_id]
            updates = dict(kwargs)
            new_schedule = updates.get("schedule", task.schedule)
            new_trigger_type = updates.get("trigger_type", task.trigger_type)
            if new_trigger_type == "queue_pending":
                new_schedule = ""
                updates["schedule"] = ""
            if new_trigger_type in {"queue_added", "queue_pending"}:
                updates["run_queue"] = True
                updates["workflow_steps"] = [{"type": "run_queue"}]
                if not str(updates.get("input_path", task.input_path) or "").strip():
                    updates["input_path"] = "queue"
            elif "workflow_steps" in updates:
                updates["workflow_steps"] = (
                    normalize_workflow_steps(
                        updates["workflow_steps"],
                        updates.get("task_type", task.task_type),
                        True,
                    )
                    if updates["workflow_steps"]
                    else []
                )
            if {"schedule", "trigger_type", "event_type"} & set(updates):
                new_schedule_rule = ScheduleParser.parse(
                    new_schedule,
                    allow_empty=new_trigger_type in {"queue_added", "queue_pending"},
                )
                if new_trigger_type == "scheduled" and new_schedule_rule.get("type") == "none":
                    raise ValueError("Scheduled trigger requires a schedule expression")
            else:
                new_schedule_rule = None

            unknown = sorted(
                key
                for key in updates
                if not hasattr(task, key) and key not in SCHEDULED_TASK_OVERRIDE_FIELDS
            )
            if unknown:
                raise TaskContractError(
                    f"Unknown scheduled task fields: {', '.join(unknown)}"
                )
            override_updates = {
                key: updates.pop(key)
                for key in list(updates)
                if key in SCHEDULED_TASK_OVERRIDE_FIELDS
            }
            candidate = ScheduledTask(
                task_id=task.id,
                name=updates.get("name", task.name),
                schedule=new_schedule,
                input_path=updates.get("input_path", task.input_path),
                profile=updates.get("profile", task.profile),
                rules_profile=updates.get("rules_profile", task.rules_profile),
                task_type=updates.get("task_type", task.task_type),
                enabled=updates.get("enabled", task.enabled),
                output_path=updates.get("output_path", task.output_path),
                workflow_steps=updates.get("workflow_steps", task.workflow_steps),
                run_queue=updates.get("run_queue", task.run_queue),
                trigger_type=new_trigger_type,
                event_type=updates.get("event_type", task.event_type),
                extra={
                    key: value
                    for key, value in task.extra.items()
                    if key not in SCHEDULED_TASK_OVERRIDE_FIELDS
                },
                **{
                    **{
                        key: value
                        for key, value in task.extra.items()
                        if key in SCHEDULED_TASK_OVERRIDE_FIELDS
                    },
                    **override_updates,
                },
            )
            updates["task_type"] = candidate.task_type
            updates["workflow_steps"] = candidate.workflow_steps
            task.extra = candidate.extra

            for key, value in updates.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            if task.trigger_type in {"queue_added", "queue_pending"}:
                task.event_type = task.trigger_type
            elif "trigger_type" in updates:
                task.event_type = ""

            # 如果更新了 schedule，重新解析
            if {"schedule", "trigger_type", "event_type"} & set(updates):
                task.schedule_rule = new_schedule_rule
                task.cron_dict = task.schedule_rule
                task._calculate_next_run()

            return True

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """获取任务"""
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[ScheduledTask]:
        """获取所有任务"""
        return list(self.tasks.values())

    def start(self):
        """启动调度器"""
        if self.running:
            return

        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._log("info", "Scheduler started")

    def stop(self):
        """停止调度器"""
        if not self.running:
            return

        self.running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._log("info", "Scheduler stopped")

    def _run_loop(self):
        """调度主循环"""
        while not self._stop_event.is_set():
            try:
                now = datetime.now()

                with self._lock:
                    self._poll_queue_triggers(now)
                    for task in self.tasks.values():
                        if task.should_run(now):
                            self._execute_task(task)

                self._stop_event.wait(2 if self.event_triggers_active and self._has_queue_triggers() else 30)

            except Exception as e:
                self._log("error", f"Scheduler error: {e}")
                time.sleep(60)

    def _execute_task(self, task: ScheduledTask):
        """执行任务"""
        self._log("info", f"Executing task: {task.name}")

        try:
            task_config = {
                "input_path": task.input_path,
                "output_path": task.output_path,
                "profile": task.profile,
                "rules_profile": task.rules_profile,
                "task_type": task.task_type,
                "task_id": task.id,
                "task_name": task.name,
                "workflow_steps": task.workflow_steps,
                "run_queue": task.run_queue,
                "trigger_type": task.trigger_type,
                "event_type": task.event_type,
            }
            # Preserve all contract fields stored by older scheduler versions,
            # including the ``lines``/``tokens`` aliases.
            task_config.update(select_task_contract_fields(task.extra))
            task.mark_run("running")

            if self.execute_callback:
                # 在新线程中执行，避免阻塞调度器
                exec_thread = threading.Thread(
                    target=self._run_task_thread,
                    args=(task, task_config),
                    daemon=True
                )
                exec_thread.start()
            else:
                self._log("warning", "No execute callback configured")
                task.last_status = "skipped"

        except Exception as e:
            self._log("error", f"Task execution failed: {task.name} - {e}")
            task.last_status = "error"

    def _run_task_thread(self, task: ScheduledTask, task_config: dict):
        """在线程中执行任务"""
        try:
            self.execute_callback(task_config)
            task.last_status = "success"
            self._log("info", f"Task completed: {task.name}")
        except Exception as e:
            task.last_status = "error"
            self._log("error", f"Task failed: {task.name} - {e}")

    def _has_queue_triggers(self) -> bool:
        return any(
            task.enabled and task.trigger_type in {"queue_added", "queue_pending"}
            for task in self.tasks.values()
        )

    def _poll_queue_triggers(self, now: datetime):
        if not self.event_triggers_active:
            self._queue_signature = None
            return
        if not self._has_queue_triggers():
            self._queue_signature = None
            return

        signature = self._read_queue_signature()
        pending_count = len(signature)
        if self._queue_signature is None:
            self._queue_signature = signature
            self._arm_queue_pending_triggers(pending_count)
            return

        old_counter = Counter(self._queue_signature)
        new_counter = Counter(signature)
        added_count = sum((new_counter - old_counter).values())
        self._queue_signature = signature
        self._arm_queue_pending_triggers(pending_count)
        self._arm_queue_added_triggers(added_count)

    def _arm_queue_added_triggers(self, added_count: int):
        if added_count <= 0:
            return
        for task in self.tasks.values():
            if task.enabled and task.trigger_type == "queue_added":
                task.notify_queue_added(added_count)
                when = task.next_run.strftime("%Y-%m-%d %H:%M") if task.next_run else "now"
                self._log("info", f"Queue-added trigger armed: {task.name} ({added_count} new task(s), next: {when})")

    def _arm_queue_pending_triggers(self, pending_count: int):
        if pending_count <= 0:
            return
        if self._queue_is_running():
            return
        for task in self.tasks.values():
            if task.enabled and task.trigger_type == "queue_pending" and not task.pending_event:
                task.notify_queue_added(pending_count)
                when = task.next_run.strftime("%Y-%m-%d %H:%M") if task.next_run else "now"
                self._log("info", f"Queue-pending trigger armed: {task.name} ({pending_count} pending task(s), next: {when})")

    @staticmethod
    def _queue_is_running() -> bool:
        try:
            from ModuleFolders.Service.TaskQueue.QueueManager import QueueManager

            return bool(QueueManager().is_running)
        except Exception:
            return False

    def _read_queue_signature(self) -> tuple:
        queue_file = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "Resource", "queue_tasks.json")
        )
        if not os.path.exists(queue_file):
            return tuple()
        try:
            with open(queue_file, "r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception as exc:
            self._log("warning", f"Failed to read queue file for trigger check: {exc}")
            return self._queue_signature or tuple()

        signature = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            status = item.get("status", "waiting")
            if status not in {"waiting", "translated"}:
                continue
            signature.append((
                str(item.get("task_type", "")),
                str(item.get("input_path", "")),
                str(item.get("output_path", "")),
                str(item.get("profile", "")),
                str(item.get("rules_profile", "")),
                str(item.get("trigger_detected_at", "")),
            ))
        return tuple(signature)

    def _log(self, level: str, message: str):
        """记录日志"""
        log_entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": message
        }
        self.logs.append(log_entry)

        # 限制日志数量
        if len(self.logs) > self.max_logs:
            self.logs = self.logs[-self.max_logs:]

    def get_logs(self, limit: int = 20) -> List[dict]:
        """获取最近的日志"""
        return self.logs[-limit:]

    def load_from_config(self, config: dict):
        """从配置加载任务"""
        scheduler_config = config.get("scheduler", {})
        tasks_data = scheduler_config.get("tasks", [])

        for task_data in tasks_data:
            try:
                task = ScheduledTask.from_dict(task_data)
                self.add_task(task)
            except Exception as e:
                self._log("error", f"Failed to load task: {e}")

    def save_to_config(self, config: dict):
        """保存任务到配置"""
        if "scheduler" not in config:
            config["scheduler"] = {}

        config["scheduler"]["tasks"] = [
            task.to_dict() for task in self.tasks.values()
        ]
        config["scheduler"]["enabled"] = self.running

    def get_status(self) -> dict:
        """获取调度器状态"""
        return {
            "running": self.running,
            "task_count": len(self.tasks),
            "enabled_count": sum(1 for t in self.tasks.values() if t.enabled),
            "next_task": self._get_next_task_info()
        }

    def _get_next_task_info(self) -> Optional[dict]:
        """获取下一个要执行的任务信息"""
        next_task = None
        next_time = None
        immediate_task = None

        for task in self.tasks.values():
            if task.enabled and task.trigger_type == "queue_pending":
                immediate_task = immediate_task or task
                if task.pending_event:
                    return {
                        "id": task.id,
                        "name": task.name,
                        "next_run": "",
                        "trigger_type": task.trigger_type,
                    }
                continue
            if task.enabled and task.next_run:
                if next_time is None or task.next_run < next_time:
                    next_time = task.next_run
                    next_task = task

        if next_task:
            return {
                "id": next_task.id,
                "name": next_task.name,
                "next_run": next_task.next_run.strftime("%Y-%m-%d %H:%M"),
                "trigger_type": next_task.trigger_type,
            }
        if immediate_task:
            return {
                "id": immediate_task.id,
                "name": immediate_task.name,
                "next_run": "",
                "trigger_type": immediate_task.trigger_type,
            }
        return None
