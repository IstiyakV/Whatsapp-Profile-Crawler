import asyncio
import base64
import random
import time
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from .config import get_settings
from .logger import log


_playwright: Any = None
_context: BrowserContext | None = None
_browser_status = {"state": "not_started", "message": "Browser has not started"}
MIN_SCREENSHOT_IMAGE_BYTES = 10_000


SPEEDS = {
    "safe": {
        "min_delay": 4000,
        "max_delay": 9000,
        "min_cooldown": 6000,
        "max_cooldown": 15000,
    },
    "balanced": {
        "min_delay": 2500,
        "max_delay": 6000,
        "min_cooldown": 3000,
        "max_cooldown": 9000,
    },
    "fast": {
        "min_delay": 1200,
        "max_delay": 3500,
        "min_cooldown": 1500,
        "max_cooldown": 5000,
    },
}


async def random_delay(min_ms: int, max_ms: int) -> None:
    await asyncio.sleep(random.randint(min_ms, max_ms) / 1000)


async def human_delay(speed: str = "balanced") -> None:
    profile = SPEEDS.get(speed, SPEEDS["balanced"])
    await random_delay(profile["min_delay"], profile["max_delay"])


async def cooldown_delay(speed: str = "balanced") -> None:
    profile = SPEEDS.get(speed, SPEEDS["balanced"])
    await random_delay(profile["min_cooldown"], profile["max_cooldown"])


async def launch_browser() -> BrowserContext:
    global _playwright, _context
    if _context:
        try:
            _ = _context.pages
            return _context
        except Exception:
            _context = None

    settings = get_settings()
    _browser_status.update({"state": "starting", "message": "Launching browser"})
    log.info("Launching persistent Chromium browser")
    _playwright = await async_playwright().start()
    _context = await _playwright.chromium.launch_persistent_context(
        str(settings.path(settings.session_dir)),
        headless=settings.headless,
        viewport={"width": 1600, "height": 1000},
        locale="en-US",
        timezone_id="Asia/Dhaka",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        ignore_default_args=["--enable-automation"],
        bypass_csp=True,
    )
    _browser_status.update({"state": "started", "message": "Browser started"})
    return _context


async def get_page() -> Page:
    global _context
    try:
        context = await launch_browser()
        for page in context.pages:
            if not page.is_closed():
                return page
        return await context.new_page()
    except Exception:
        _browser_status.update({"state": "restarting", "message": "Browser was closed. Restarting browser."})
        _context = None
        context = await launch_browser()
        return await context.new_page()


async def login_required(page: Page) -> bool:
    try:
        qr_canvas = page.locator('canvas[aria-label="Scan this QR code to link a device!"]')
        if await _is_visible(qr_canvas, 1000):
            return True
        text = ((await page.locator("body").text_content(timeout=2000)) or "").lower()
        login_markers = [
            "scan this qr code",
            "link a device",
            "use whatsapp on your computer",
            "log in",
        ]
        return any(marker in text for marker in login_markers)
    except Exception:
        return False


async def wait_for_login(page: Page) -> None:
    _browser_status.update({"state": "opening_whatsapp", "message": "Opening WhatsApp Web"})
    log.info("Opening WhatsApp Web")
    await page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60_000)
    chat_list = page.locator('[data-testid="chat-list"]')
    qr_canvas = page.locator('canvas[aria-label="Scan this QR code to link a device!"]')

    logged_in = False
    qr_needed = False
    try:
        await chat_list.wait_for(state="visible", timeout=30_000)
        logged_in = True
    except Exception:
        try:
            await qr_canvas.wait_for(state="visible", timeout=2_000)
            qr_needed = True
        except Exception:
            pass

    if logged_in:
        _browser_status.update({"state": "logged_in", "message": "WhatsApp Web is logged in"})
        log.info("WhatsApp session is already logged in")
        return

    if qr_needed:
        _browser_status.update({"state": "waiting_qr", "message": "Waiting for QR scan"})
        log.info("Scan the QR code in the browser window")
    await chat_list.wait_for(state="visible", timeout=180_000)
    _browser_status.update({"state": "logged_in", "message": "WhatsApp Web is ready"})
    log.info("WhatsApp Web is ready")


def get_browser_status() -> dict[str, str]:
    return dict(_browser_status)


async def _download_locator_image(page: Page, locator: Any, image_path: Path) -> bool:
    try:
        src = await locator.get_attribute("src")
        if not src or "pps.whatsapp.net" not in src:
            return False
        encoded = await page.evaluate(
            """
            async (src) => {
              const res = await fetch(src);
              if (!res.ok) return null;
              const blob = await res.blob();
              return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result.split(',')[1]);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
              });
            }
            """,
            src,
        )
        if not encoded:
            return False
        image_path.write_bytes(base64.b64decode(encoded))
        return image_path.exists() and image_path.stat().st_size > 0
    except Exception:
        return False


async def _wait_for_locator_image_ready(locator: Any, timeout_ms: int = 8000) -> bool:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        try:
            ready = await locator.evaluate(
                """
                (el) => {
                  const style = window.getComputedStyle(el);
                  const box = el.getBoundingClientRect();
                  if (style.visibility === 'hidden' || Number(style.opacity || 1) < 0.95) return false;
                  if (box.width < 80 || box.height < 80) return false;
                  if (el.tagName && el.tagName.toLowerCase() === 'img') {
                    return el.complete && el.naturalWidth > 0 && el.naturalHeight > 0;
                  }
                  return true;
                }
                """
            )
            if ready:
                first_box = await locator.bounding_box()
                await random_delay(700, 1000)
                second_box = await locator.bounding_box()
                if not first_box or not second_box:
                    continue
                if abs(first_box["width"] - second_box["width"]) < 2 and abs(first_box["height"] - second_box["height"]) < 2:
                    return True
        except Exception:
            pass
        await random_delay(250, 400)
    return False


def _screenshot_capture_looks_ready(image_path: Path) -> bool:
    try:
        return image_path.exists() and image_path.stat().st_size >= MIN_SCREENSHOT_IMAGE_BYTES
    except Exception:
        return False


async def _capture_locator_screenshot(locator: Any, image_path: Path) -> bool:
    await _wait_for_locator_image_ready(locator)
    for _ in range(3):
        await random_delay(800, 1300)
        await locator.screenshot(path=str(image_path), type="png")
        if _screenshot_capture_looks_ready(image_path):
            return True
    try:
        image_path.unlink(missing_ok=True)
    except Exception:
        pass
    return False


async def navigate_to_chat(page: Page, phone: str, speed: str) -> dict[str, Any]:
    url = f"https://web.whatsapp.com/send?phone={phone.replace('+', '')}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await human_delay(speed)

        if await login_required(page):
            _browser_status.update({"state": "waiting_qr", "message": "Session expired. Scan QR code to continue."})
            await wait_for_login(page)
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await human_delay(speed)

        invalid_popup = page.locator('div[data-testid="popup-contents"]')
        if await _is_visible(invalid_popup, 5_000):
            text = await invalid_popup.text_content() or ""
            if "invalid" in text.lower():
                ok_button = page.get_by_role("button", name="OK")
                if await _is_visible(ok_button, 2_000):
                    await ok_button.click()
                return {"success": False, "reason": "invalid_number", "error": "Phone number is invalid"}

        header = page.locator('[data-testid="conversation-header"]')
        try:
            await header.wait_for(state="visible", timeout=15_000)
            return {"success": True}
        except Exception:
            body = (await page.locator("body").text_content()) or ""
            if "phone number shared via url is invalid" in body.lower():
                return {"success": False, "reason": "invalid_number", "error": "Phone number is invalid"}
            return {"success": False, "reason": "not_on_whatsapp", "error": "Number not found on WhatsApp"}
    except Exception as exc:
        return {"success": False, "reason": "error", "error": str(exc)}


async def _is_visible(locator: Any, timeout: int = 3000) -> bool:
    try:
        return await locator.is_visible(timeout=timeout)
    except Exception:
        return False


async def open_profile_panel(page: Page, speed: str) -> bool:
    targets = [
        page.locator("#main header img").first,
        page.locator('#main header span[dir="auto"][title]').first,
        page.locator("#main header").first,
        page.locator('[data-testid="conversation-contact-header-info"]'),
        page.locator('[data-testid="conversation-info-header"]'),
        page.locator('[data-testid="conversation-header"] span[dir="auto"]'),
        page.locator("header span[title]").first,
    ]
    for target_factory in targets:
        target = target_factory() if callable(target_factory) else target_factory
        if await _is_visible(target, 3000):
            try:
                await target.click(force=True, delay=100)
                await human_delay(speed)
                section = page.locator("section")
                await section.wait_for(state="visible", timeout=8000)
                return True
            except Exception:
                continue
    try:
        await page.evaluate(
            """
            () => {
              const header = document.querySelector('#main header') || document.querySelector('header');
              if (header) header.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
            }
            """
        )
        await human_delay(speed)
        await page.locator("section").wait_for(state="visible", timeout=8000)
        return True
    except Exception:
        pass
    return False


async def close_profile_panel(page: Page) -> None:
    try:
        close_button = page.locator('section [data-testid="btn-closer-drawer"], section [data-icon="x"]').first
        if await _is_visible(close_button, 2000):
            await close_button.click()
            await random_delay(500, 1000)
    except Exception:
        pass


async def collect_public_profile_details(page: Page, phone: str) -> dict[str, Any]:
    clean_query = phone.replace("+", "")
    details: dict[str, Any] = {"public_text": []}
    try:
        lines = await page.evaluate(
            """
            () => {
              const section = document.querySelector('section');
              if (!section) return [];
              const text = section.innerText || '';
              return text.split('\\n').map(v => v.trim()).filter(Boolean);
            }
            """
        )
        unique: list[str] = []
        for line in lines:
            stripped = line.replace(" ", "").replace("+", "").replace("-", "")
            if clean_query in stripped:
                continue
            if line not in unique:
                unique.append(line)
        details["public_text"] = unique
        if unique:
            details["display_name"] = unique[0]
        if len(unique) > 1:
            details["about"] = " | ".join(unique[1:])
    except Exception:
        pass

    try:
        links = await page.evaluate(
            """
            () => {
              const section = document.querySelector('section');
              if (!section) return [];
              return Array.from(section.querySelectorAll('a[href]')).map(a => ({
                text: (a.innerText || a.textContent || '').trim(),
                href: a.href
              })).filter(v => v.text || v.href);
            }
            """
        )
        if links:
            details["links"] = links
    except Exception:
        pass

    return details


async def capture_cover_image(page: Page, task_id: int, phone: str, profile_img: Any | None) -> str | None:
    try:
        settings = get_settings()
        image_dir = settings.path(settings.images_dir) / "profiles"
        image_dir.mkdir(parents=True, exist_ok=True)
        cover_path = image_dir / f"task-{task_id}_{phone.replace('+', '')}_cover.png"
        profile_src = await profile_img.get_attribute("src") if profile_img else None
        images = await page.locator("section img").all()
        best = None
        best_area = 0
        for image in images:
            if not await _is_visible(image, 1000):
                continue
            src = await image.get_attribute("src")
            if src and profile_src and src == profile_src:
                continue
            box = await image.bounding_box()
            if not box:
                continue
            area = box["width"] * box["height"]
            if area > best_area and box["width"] >= 140 and box["height"] >= 80:
                best = image
                best_area = area
        if not best:
            return None
        if not await _download_locator_image(page, best, cover_path):
            await _capture_locator_screenshot(best, cover_path)
        return str(cover_path) if cover_path.exists() and cover_path.stat().st_size > 0 else None
    except Exception:
        return None


async def find_large_image_viewer(page: Page) -> Any | None:
    overlay_selectors = [
        '[data-animate-modal-body="true"] img',
        'div[role="dialog"] img[src*="pps.whatsapp.net"]',
        'div[role="dialog"] img',
        '[data-testid="media-viewer"] img',
        '[data-testid="image-viewer"] img',
        '[data-testid="media-canvas"] img',
        'div[tabindex="-1"][role="dialog"] img',
        '[data-animate-modal-body="true"] canvas',
        'div[role="dialog"] canvas',
    ]
    best = None
    best_area = 0
    for selector in overlay_selectors:
        candidates = await page.locator(selector).all()
        for candidate in candidates:
            if not await _is_visible(candidate, 500):
                continue
            box = await candidate.bounding_box()
            if not box:
                continue
            area = box["width"] * box["height"]
            try:
                src = await candidate.get_attribute("src")
                if src and "pps.whatsapp.net" in src:
                    area *= 2
            except Exception:
                pass
            if area > best_area and box["width"] >= 160 and box["height"] >= 160:
                best = candidate
                best_area = area
    return best


async def open_large_profile_image(page: Page, profile_img: Any) -> Any | None:
    existing = await find_large_image_viewer(page)
    if existing:
        await _wait_for_locator_image_ready(existing)
        return existing

    try:
        await profile_img.locator("..").click(delay=150)
    except Exception:
        try:
            await profile_img.click(delay=100, timeout=3000)
        except Exception:
            pass

    await page.mouse.move(0, 0)
    for _ in range(6):
        viewer = await find_large_image_viewer(page)
        if viewer:
            await _wait_for_locator_image_ready(viewer)
            return viewer
        await random_delay(500, 700)

    try:
        await page.evaluate(
            """
            () => {
              const section = document.querySelector('section');
              if (!section) return;
              const img = section.querySelector('img[src*="pps.whatsapp.net"]') || section.querySelector('img');
              if (!img) return;
              const btn = img.closest('[role="button"]') || img.closest('[data-testid]') || img.parentElement;
              if (btn) btn.click();
            }
            """
        )
    except Exception:
        return None

    await page.mouse.move(0, 0)
    for _ in range(6):
        viewer = await find_large_image_viewer(page)
        if viewer:
            await _wait_for_locator_image_ready(viewer)
            return viewer
        await random_delay(500, 700)
    return None


async def extract_profile_picture(page: Page, task_id: int, phone: str, speed: str) -> dict[str, Any]:
    try:
        if not await open_profile_panel(page, speed):
            return {"success": False, "reason": "no_profile_panel", "error": "Could not open profile panel"}

        await random_delay(1000, 2000)
        name = None
        about = None
        details = await collect_public_profile_details(page, phone)
        try:
            unique = details.get("public_text", [])
            if unique:
                name = unique[0]
                about = " | ".join(unique[1:]) if len(unique) > 1 else None
        except Exception:
            pass

        profile_img = None
        for selector in [
            'section img[src*="pps.whatsapp.net"]',
            'section [data-testid="image-thumb"] img[src*="pps.whatsapp.net"]',
            'section [role="button"] img[src*="pps.whatsapp.net"]',
        ]:
            candidate = page.locator(selector).first
            if await _is_visible(candidate, 3000):
                profile_img = candidate
                break

        if profile_img is None:
            default_avatar = page.locator('section [data-testid="default-user"], section svg').first
            if await _is_visible(default_avatar, 3000):
                await close_profile_panel(page)
                return {
                    "success": False,
                    "reason": "hidden_dp",
                    "error": "Profile picture is hidden or not set",
                    "name": name,
                    "about": about,
                    "details": details,
                }
            any_img = page.locator("section img").first
            if await _is_visible(any_img, 3000):
                profile_img = any_img
            else:
                await close_profile_panel(page)
                return {
                    "success": False,
                    "reason": "hidden_dp",
                    "error": "No profile picture found",
                    "name": name,
                    "about": about,
                    "details": details,
                }

        cover_image_path = await capture_cover_image(page, task_id, phone, profile_img)

        full_size = await open_large_profile_image(page, profile_img)

        settings = get_settings()
        image_dir = settings.path(settings.images_dir) / "profiles"
        image_dir.mkdir(parents=True, exist_ok=True)
        image_path = image_dir / f"task-{task_id}_{phone.replace('+', '')}.png"

        saved_profile_image = False
        if full_size:
            saved_profile_image = await _download_locator_image(page, full_size, image_path)
            if not saved_profile_image:
                saved_profile_image = await _capture_locator_screenshot(full_size, image_path)
        if not saved_profile_image:
            saved_profile_image = await _download_locator_image(page, profile_img, image_path)
        if not saved_profile_image:
            saved_profile_image = await _capture_locator_screenshot(profile_img, image_path)
        if not saved_profile_image:
            await page.keyboard.press("Escape")
            await random_delay(500, 1000)
            await close_profile_panel(page)
            return {
                "success": False,
                "reason": "hidden_dp",
                "error": "Profile picture is hidden, unavailable, or still loading",
                "name": name,
                "about": about,
                "details": details,
                "cover_image_path": cover_image_path,
            }

        await page.keyboard.press("Escape")
        await random_delay(500, 1000)
        await close_profile_panel(page)
        return {
            "success": True,
            "image_path": str(image_path),
            "name": name,
            "about": about,
            "details": details,
            "cover_image_path": cover_image_path,
        }
    except Exception as exc:
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        await close_profile_panel(page)
        return {"success": False, "reason": "error", "error": str(exc)}


async def take_debug_screenshot(page: Page, phone: str) -> str | None:
    try:
        settings = get_settings()
        debug_dir = settings.path(settings.images_dir) / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        base = debug_dir / f"{phone.replace('+', '')}_error_{int(time.time() * 1000)}"
        await page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        base.with_suffix(".html").write_text(await page.content(), encoding="utf-8")
        return str(base.with_suffix(".png"))
    except Exception:
        return None


async def close_browser() -> None:
    global _context, _playwright
    if _context:
        await _context.close()
        _context = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
    _browser_status.update({"state": "closed", "message": "Browser closed"})
