"""Server entry point for the task service.

Usage:
    python -m tests.demo_projects.task_service.server <command> [args]
    python server.py create "Fix login bug" --assignee alice
    python server.py stats

This is a CLI simulating what would eventually be an API server.
"""

import sys

from .models import TaskStore
from .service import TaskService


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python server.py <command> [args]")
        print("Commands: create, start, complete, stats, user")
        sys.exit(1)

    store = TaskStore()
    svc = TaskService(store)
    command = sys.argv[1]

    if command == "create":
        title = sys.argv[2] if len(sys.argv) > 2 else "Untitled"
        assignee = None
        if "--assignee" in sys.argv:
            idx = sys.argv.index("--assignee")
            if idx + 1 < len(sys.argv):
                assignee = sys.argv[idx + 1]
        task = svc.add_task(title, assignee)
        print(f"Created: {task.id} — {task.title}")

    elif command == "start":
        task_id = sys.argv[2] if len(sys.argv) > 2 else ""
        task = svc.start_task(task_id)
        print(f"Started: {task.id} — status: {task.status}")

    elif command == "complete":
        task_id = sys.argv[2] if len(sys.argv) > 2 else ""
        task = svc.complete_task(task_id)
        print(f"Completed: {task.id} — status: {task.status}")

    elif command == "stats":
        dashboard = svc.get_dashboard()
        print(f"Total: {dashboard['total']}")
        print(f"Done: {dashboard['done']}")
        print(f"Oldest pending: {dashboard['oldest_pending']}")

    elif command == "user":
        assignee = sys.argv[2] if len(sys.argv) > 2 else ""
        tasks = svc.get_user_tasks(assignee)
        for t in tasks:
            print(f"  {t.id}: {t.title} [{t.status}]")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
