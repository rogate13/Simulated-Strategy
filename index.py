import datetime
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional


# =========================
# Logging Configuration
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("task-service")


# =========================
# User Management & Quota
# =========================

class UserQuotaManager:
    def __init__(self, users: Dict[str, Dict[str, int]]):
        self.users = users
        self.last_reset_date: Optional[datetime.date] = None

    def reset_daily_quota_if_needed(self, current_date: datetime.date) -> None:
        """
        Reset executed count once per day.
        """
        if self.last_reset_date != current_date:
            logger.info("Resetting daily user quota counters")

            for username in self.users:
                self.users[username]["executed"] = 0

            self.last_reset_date = current_date

    def user_exists(self, username: str) -> bool:
        return username in self.users

    def can_execute(self, username: str) -> bool:
        if not self.user_exists(username):
            logger.error("User '%s' does not exist", username)
            return False

        user_data = self.users[username]
        return user_data["executed"] < user_data["quota"]

    def record_execution(self, username: str) -> None:
        if not self.user_exists(username):
            raise ValueError(f"Cannot record execution. User '{username}' does not exist.")

        self.users[username]["executed"] += 1

    def get_usage(self, username: str) -> Dict[str, int]:
        if not self.user_exists(username):
            raise ValueError(f"User '{username}' does not exist.")

        return self.users[username]


# =========================
# Task Data Model
# =========================

@dataclass
class Task:
    id: str
    user: str
    time: str
    action: str
    target: str
    params: Dict[str, Any] = field(default_factory=dict)
    last_executed_date: Optional[datetime.date] = None

    def is_due(self, current_time: str, current_date: datetime.date) -> bool:
        """
        Task is due if:
        - scheduled time equals current time
        - task has not been executed today
        """
        return self.time == current_time and self.last_executed_date != current_date

    def mark_executed(self, current_date: datetime.date) -> None:
        self.last_executed_date = current_date


# =========================
# Action Strategies
# =========================

class ActionStrategy:
    """
    Base class for all task actions.
    """

    def execute(self, task: Task) -> None:
        raise NotImplementedError("ActionStrategy must implement execute method")


class SyncAction(ActionStrategy):
    def execute(self, task: Task) -> None:
        dry_run = task.params.get("dry_run", False)

        if dry_run:
            logger.info("[DRY RUN] Syncing target '%s' for user '%s'", task.target, task.user)
        else:
            logger.info("Syncing target '%s' for user '%s'", task.target, task.user)


class BackupAction(ActionStrategy):
    def execute(self, task: Task) -> None:
        compression = task.params.get("compression", "none")

        logger.info(
            "Backing up target '%s' for user '%s' with compression='%s'",
            task.target,
            task.user,
            compression
        )


class DeleteAction(ActionStrategy):
    def execute(self, task: Task) -> None:
        confirm = task.params.get("confirm", False)

        if not confirm:
            raise ValueError(
                f"Delete action for target '{task.target}' requires params.confirm=True"
            )

        logger.info("Deleting target '%s' for user '%s'", task.target, task.user)


# =========================
# Task Executor
# =========================

class TaskExecutor:
    def __init__(self, quota_manager: UserQuotaManager):
        self.quota_manager = quota_manager
        self.actions: Dict[str, ActionStrategy] = {}

    def register_action(self, action_name: str, strategy: ActionStrategy) -> None:
        self.actions[action_name] = strategy

    def execute(self, task: Task, current_date: datetime.date) -> None:
        logger.info(
            "Preparing to execute task_id='%s', user='%s', action='%s'",
            task.id,
            task.user,
            task.action
        )

        if not self.quota_manager.user_exists(task.user):
            logger.error(
                "Task '%s' skipped because user '%s' does not exist",
                task.id,
                task.user
            )
            return

        if not self.quota_manager.can_execute(task.user):
            logger.warning(
                "Task '%s' skipped. User '%s' has exceeded quota.",
                task.id,
                task.user
            )
            return

        action_strategy = self.actions.get(task.action)

        if action_strategy is None:
            logger.error(
                "Task '%s' skipped. Unsupported action '%s'",
                task.id,
                task.action
            )
            return

        try:
            action_strategy.execute(task)

            self.quota_manager.record_execution(task.user)
            task.mark_executed(current_date)

            usage = self.quota_manager.get_usage(task.user)

            logger.info(
                "Task '%s' executed successfully. User '%s' usage: %s/%s",
                task.id,
                task.user,
                usage["executed"],
                usage["quota"]
            )

        except Exception as error:
            logger.exception(
                "Task '%s' failed during execution. Reason: %s",
                task.id,
                error
            )


# =========================
# Simple Scheduling System
# =========================

class SimpleScheduler:
    def __init__(self, tasks: list[Task], executor: TaskExecutor, quota_manager: UserQuotaManager):
        self.tasks = tasks
        self.executor = executor
        self.quota_manager = quota_manager

    def run_pending(self, now: Optional[datetime.datetime] = None) -> None:
        """
        Simple scheduler implementation.
        It checks current HH:MM and executes matching tasks.
        """

        now = now or datetime.datetime.now()

        current_time = now.strftime("%H:%M")
        current_date = now.date()

        self.quota_manager.reset_daily_quota_if_needed(current_date)

        logger.info("Checking pending tasks for time '%s'", current_time)

        for task in self.tasks:
            if task.is_due(current_time, current_date):
                self.executor.execute(task, current_date)


# =========================
# Example Usage
# =========================

if __name__ == "__main__":
    users = {
        "alice": {"quota": 3, "executed": 0},
        "bob": {"quota": 5, "executed": 0}
    }

    tasks = [
        Task(
            id="task-001",
            user="alice",
            time="12:00",
            action="sync",
            target="/data/x",
            params={"dry_run": True}
        ),
        Task(
            id="task-002",
            user="bob",
            time="12:00",
            action="backup",
            target="/srv/y",
            params={"compression": "gzip"}
        ),
        Task(
            id="task-003",
            user="alice",
            time="12:00",
            action="delete",
            target="/tmp/z",
            params={"confirm": True}
        ),
    ]

    quota_manager = UserQuotaManager(users)

    executor = TaskExecutor(quota_manager)
    executor.register_action("sync", SyncAction())
    executor.register_action("backup", BackupAction())
    executor.register_action("delete", DeleteAction())

    scheduler = SimpleScheduler(tasks, executor, quota_manager)

    # For real usage:
    # scheduler.run_pending()

    # For testing/demo:
    demo_time = datetime.datetime.strptime("2026-05-08 12:00", "%Y-%m-%d %H:%M")
    scheduler.run_pending(demo_time)