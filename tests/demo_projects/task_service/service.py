"""Service layer — orchestrates task operations."""

from typing import Optional

from .models import Task, TaskStore


class TaskService:
    """Business logic for task management."""

    def __init__(self, store: Optional[TaskStore] = None) -> None:
        self.store = store or TaskStore()

    def add_task(self, title: str, assignee: Optional[str] = None) -> Task:
        return self.store.create(title, assignee)

    def start_task(self, task_id: str) -> Task:
        task = self.store.update_status(task_id, "in_progress")
        return task  # BUG: task could be None

    def complete_task(self, task_id: str) -> Task:
        task = self.store.update_status(task_id, "done")
        return task  # BUG: task could be None

    def get_user_tasks(self, assignee: str) -> list[Task]:
        return self.store.list_by_assignee(assignee)

    def get_dashboard(self) -> dict:
        return self.store.get_stats()
