from typing import Any

from . import storage
from .config import get_settings
from .events import event_hub
from .logger import log
from .whatsapp import cooldown_delay, extract_profile_picture, get_page, navigate_to_chat, take_debug_screenshot, wait_for_login


FINAL_STATUSES = {"success", "no_whatsapp", "hidden_dp"}


def get_skippable_global(phone: str, skip_checked: bool) -> dict[str, Any] | None:
    if not skip_checked:
        return None
    existing = storage.get_latest_global_result(phone)
    if existing and existing["status"] in FINAL_STATUSES:
        return existing
    return None


async def crawl_number(task_id: int, phone: str, speed: str, force: bool = False) -> dict[str, Any]:
    existing = storage.get_result(task_id, phone)
    settings = get_settings()
    if not force and existing and existing["status"] in FINAL_STATUSES:
        return existing

    retry_count = 0 if force or not existing else int(existing["retries"] or 0)
    storage.upsert_result(task_id, phone, "pending", retries=retry_count)
    await event_hub.publish("task_progress", {"task_id": task_id, "phone": phone, "status": "pending"})

    try:
        page = await get_page()
        await wait_for_login(page)

        nav = await navigate_to_chat(page, phone, speed)
        if not nav["success"]:
            reason = nav.get("reason")
            status = "no_whatsapp" if reason in {"invalid_number", "not_on_whatsapp"} else "error"
            result = storage.upsert_result(
                task_id,
                phone,
                status,
                error_msg=nav.get("error") or "Navigation failed",
                retries=retry_count,
            )
            return result or {}

        extraction = await extract_profile_picture(page, task_id, phone, speed)
        if extraction["success"]:
            result = storage.upsert_result(
                task_id,
                phone,
                "success",
                image_path=extraction.get("image_path"),
                name=extraction.get("name"),
                about=extraction.get("about"),
                details=extraction.get("details"),
                cover_image_path=extraction.get("cover_image_path"),
                retries=retry_count,
            )
            return result or {}

        if extraction.get("reason") == "hidden_dp":
            result = storage.upsert_result(
                task_id,
                phone,
                "hidden_dp",
                name=extraction.get("name"),
                about=extraction.get("about"),
                details=extraction.get("details"),
                cover_image_path=extraction.get("cover_image_path"),
                error_msg=extraction.get("error"),
                retries=retry_count,
            )
            return result or {}

        raise RuntimeError(extraction.get("error") or "Extraction failed")
    except Exception as exc:
        msg = str(exc)
        log.exception("Crawl failed for %s", phone)
        try:
            await take_debug_screenshot(page, phone)
        except Exception:
            pass
        new_retry_count = retry_count + 1
        if new_retry_count < settings.max_retries:
            status = "error"
            error_msg = msg
        else:
            status = "error"
            error_msg = f"Max retries: {msg}"
        result = storage.upsert_result(task_id, phone, status, error_msg=error_msg, retries=new_retry_count)
        return result or {}
    finally:
        await cooldown_delay(speed)
