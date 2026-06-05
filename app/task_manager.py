import asyncio
from dataclasses import dataclass
from typing import Any

from . import storage
from .crawler import crawl_number, get_skippable_global
from .events import event_hub
from .logger import log


@dataclass
class RuntimeState:
    runner: asyncio.Task | None = None
    cancel_requested: bool = False


class TaskManager:
    def __init__(self) -> None:
        self._states: dict[int, RuntimeState] = {}
        self._lock = asyncio.Lock()

    async def create_task(
        self,
        name: str,
        numbers: list[str],
        source_type: str,
        source_filename: str | None,
        speed: str,
        skip_checked: bool,
        max_errors: int = 0,
    ) -> dict[str, Any]:
        task = storage.create_task(name, numbers, source_type, source_filename, speed, skip_checked, max_errors)
        await event_hub.publish("task_created", {"task": task})
        await self.start_task(int(task["id"]))
        return task

    async def start_task(self, task_id: int) -> None:
        async with self._lock:
            state = self._states.get(task_id)
            if state and state.runner and not state.runner.done():
                return
            state = RuntimeState()
            state.runner = asyncio.create_task(self._run_task(task_id))
            self._states[task_id] = state

    async def pause_task(self, task_id: int) -> dict[str, Any] | None:
        task = storage.update_task(task_id, status="paused")
        await event_hub.publish("task_updated", {"task": task})
        return task

    async def resume_task(self, task_id: int) -> dict[str, Any] | None:
        task = storage.update_task(task_id, status="queued")
        await event_hub.publish("task_updated", {"task": task})
        await self.start_task(task_id)
        return task

    async def cancel_task(self, task_id: int) -> dict[str, Any] | None:
        state = self._states.setdefault(task_id, RuntimeState())
        state.cancel_requested = True
        task = storage.update_task(task_id, status="cancelled", finished_at="datetime('now')")
        await event_hub.publish("task_updated", {"task": task})
        return task

    async def retry_errors(self, task_id: int) -> dict[str, Any] | None:
        count = storage.retry_error_numbers(task_id)
        task = storage.update_task(task_id, status="queued", finished_at=None, current_phone=None)
        await event_hub.publish("task_updated", {"task": task, "retry_count": count})
        await self.start_task(task_id)
        return task

    async def _run_task(self, task_id: int) -> None:
        state = self._states.setdefault(task_id, RuntimeState())
        task = storage.get_task(task_id)
        if not task:
            return

        if task["status"] in {"cancelled", "completed"}:
            return

        task = storage.update_task(task_id, status="running", started_at=task["started_at"] or "datetime('now')")
        await event_hub.publish("task_updated", {"task": task})

        try:
            while True:
                task = storage.get_task(task_id)
                if not task:
                    return
                if state.cancel_requested or task["status"] == "cancelled":
                    storage.update_task(task_id, status="cancelled", finished_at="datetime('now')")
                    return
                if task["status"] == "paused":
                    await event_hub.publish("task_updated", {"task": task})
                    return

                phone = storage.next_pending_number(task_id)
                if not phone:
                    task = storage.update_task(task_id, status="completed", current_phone=None, finished_at="datetime('now')")
                    await event_hub.publish("task_completed", {"task": task})
                    return

                storage.update_task(task_id, current_phone=phone)
                await event_hub.publish("task_progress", {"task_id": task_id, "phone": phone, "status": "running"})

                previous = get_skippable_global(phone, bool(task["skip_checked"]))
                if previous:
                    storage.mark_task_number(task_id, phone, previous["status"])
                    storage.upsert_result(
                        task_id,
                        phone,
                        previous["status"],
                        image_path=previous.get("image_path"),
                        name=previous.get("name"),
                        about=previous.get("about"),
                        details={"raw": previous.get("details_json")} if previous.get("details_json") else None,
                        cover_image_path=previous.get("cover_image_path"),
                        error_msg=previous.get("error_msg"),
                        retries=previous.get("retries") or 0,
                    )
                else:
                    result = await crawl_number(task_id, phone, task["speed"])
                    storage.mark_task_number(task_id, phone, result.get("status", "error"))

                updated = storage.recalc_task_counts(task_id)
                await event_hub.publish("task_progress", {"task": updated, "last_phone": phone})
                if updated and int(updated.get("max_errors") or 0) > 0 and int(updated.get("error_count") or 0) >= int(updated["max_errors"]):
                    task = storage.update_task(task_id, status="paused", current_phone=None)
                    await event_hub.publish("task_updated", {"task": task, "reason": "max_errors"})
                    return
        except Exception as exc:
            log.exception("Task %s failed", task_id)
            if not storage.get_task(task_id):
                return
            task = storage.update_task(task_id, status="error", finished_at="datetime('now')")
            await event_hub.publish("task_error", {"task": task, "error": str(exc)})


task_manager = TaskManager()
