"""Task data model and repository."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Task:
    title: str
    status: str = "pending"  # pending, in_progress, done
    assignee: Optional[str] = None
    id: str = ""


class TaskStore:
    """In-memory task storage."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._counter = 0

    def create(self, title: str, assignee: Optional[str] = None) -> Task:
        self._counter += 1
        task = Task(
            id=f"TASK-{self._counter:03d}",
            title=title,
            assignee=assignee,
            status="pending",
        )
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def update_status(self, task_id: str, new_status: str) -> Task:
        """Update task status. Returns the updated task.

        BUG: if task_id doesn't exist, returns None, and caller
        tries to access .status on None → AttributeError.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None  # BUG: should raise KeyError or return Optional
        task.status = new_status
        return task

    def list_by_assignee(self, assignee: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.assignee == assignee]

    def get_stats(self) -> dict:
        """Return task statistics.

        BUG: tries to call .get() on a list (wrong API), raising AttributeError.
        """
        done_count = len([t for t in self._tasks.values()
                         if t.status == "done"])
        # BUG: list has no .get() method
        pending_list = [t for t in self._tasks.values()
                       if t.status == "pending"]
        oldest_pending = pending_list.get(0)  # BUG: list.get() doesn't exist
        return {
            "total": len(self._tasks),
            "done": done_count,
            "oldest_pending": oldest_pending,
        }
