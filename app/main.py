import csv
import io
import json
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import storage
from .config import ensure_runtime_dirs, get_settings
from .events import event_hub
from .parser import parse_upload, normalize_phone
from .schemas import RangeLookup, SingleLookup, TaskCreate, TaskRename
from .task_manager import task_manager
from .whatsapp import get_browser_status


ensure_runtime_dirs()
storage.init_db()

app = FastAPI(title="WhatsApp Crawler Python")
settings = get_settings()
static_dir = Path(__file__).parent / "static"

app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/images", StaticFiles(directory=settings.path(settings.images_dir)), name="images")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (static_dir / "index.html").read_text(encoding="utf-8")


@app.get("/api/events")
async def events() -> StreamingResponse:
    return StreamingResponse(event_hub.stream(), media_type="text/event-stream")


@app.get("/api/stats")
async def stats():
    data = storage.get_stats()
    data["browser"] = get_browser_status()
    return data


@app.get("/api/browser/status")
async def browser_status():
    return get_browser_status()


@app.get("/api/tasks")
async def tasks():
    return {"data": storage.list_tasks()}


@app.get("/api/tasks/{task_id}")
async def task(task_id: int):
    item = storage.get_task(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")
    return item


@app.post("/api/tasks/preview")
async def preview_task(file: UploadFile = File(...)):
    content = await file.read()
    parsed = parse_upload(file.filename or "numbers.txt", content)
    return {
        "filename": file.filename,
        "total": len(parsed.numbers),
        "duplicates_removed": parsed.duplicates_removed,
        "invalid_rows": parsed.invalid_rows,
        "detected_column": parsed.detected_column,
        "preview": parsed.preview,
        "numbers": parsed.numbers,
    }


@app.post("/api/tasks")
async def create_task(payload: TaskCreate):
    numbers = []
    for raw in payload.numbers:
        phone = normalize_phone(raw)
        if phone and phone not in numbers:
            numbers.append(phone)
    if not numbers:
        raise HTTPException(status_code=400, detail="No valid phone numbers supplied")
    task = await task_manager.create_task(
        name=payload.name,
        numbers=numbers,
        source_type="upload",
        source_filename=None,
        speed=payload.speed,
        skip_checked=payload.skip_checked,
        max_errors=payload.max_errors,
    )
    return {"success": True, "task": task}


@app.post("/api/tasks/{task_id}/pause")
async def pause_task(task_id: int):
    return {"success": True, "task": await task_manager.pause_task(task_id)}


@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: int):
    return {"success": True, "task": await task_manager.resume_task(task_id)}


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: int):
    return {"success": True, "task": await task_manager.cancel_task(task_id)}


@app.patch("/api/tasks/{task_id}")
async def rename_task(task_id: int, payload: TaskRename):
    if not storage.get_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    task = storage.update_task(task_id, name=payload.name)
    await event_hub.publish("task_updated", {"task": task})
    return {"success": True, "task": task}


@app.post("/api/tasks/{task_id}/retry-errors")
async def retry_task_errors(task_id: int):
    if not storage.get_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "task": await task_manager.retry_errors(task_id)}


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int):
    if not storage.get_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    await task_manager.cancel_task(task_id)
    images_root = settings.path(settings.images_dir).resolve()
    allowed_prefix = f"task-{task_id}_"
    for image_path in storage.get_task_image_paths(task_id):
        path = Path(image_path).resolve()
        try:
            if (
                path.exists()
                and path.is_file()
                and path.is_relative_to(images_root)
                and (path.parent.name == f"task-{task_id}" or path.name.startswith(allowed_prefix))
            ):
                path.unlink()
        except OSError:
            pass
    deleted = storage.delete_task(task_id)
    await event_hub.publish("task_deleted", {"task_id": task_id})
    return {"success": deleted}


@app.get("/api/tasks/{task_id}/results")
async def task_results(task_id: int, page: int = 1, limit: int = 100, status: str = "", search: str = ""):
    if not storage.get_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    results = storage.get_results(task_id, page, limit, status, search)
    for row in results["data"]:
        image_too_small = False
        if row.get("image_path"):
            try:
                image_too_small = Path(row["image_path"]).stat().st_size < 10_000
            except OSError:
                image_too_small = True
            row["image_url"] = "/images/" + str(Path(row["image_path"]).relative_to(settings.path(settings.images_dir))).replace("\\", "/")
        else:
            row["image_url"] = None
        if row.get("cover_image_path"):
            row["cover_image_url"] = "/images/" + str(Path(row["cover_image_path"]).relative_to(settings.path(settings.images_dir))).replace("\\", "/")
        else:
            row["cover_image_url"] = None
        row["display_image_url"] = row["cover_image_url"] if image_too_small and row["cover_image_url"] else (row["image_url"] or row["cover_image_url"])
        if row.get("details_json"):
            try:
                row["details"] = json.loads(row["details_json"])
            except json.JSONDecodeError:
                row["details"] = {"raw": row["details_json"]}
        else:
            row["details"] = {}
    return results


@app.get("/api/tasks/{task_id}/export.csv")
async def export_task(task_id: int, status: str = "", search: str = ""):
    results = storage.get_results(task_id, page=1, limit=1_000_000, status=status, search=search)
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["phone", "status", "name", "about", "details_json", "image_path", "cover_image_path", "error_msg", "retries", "updated_at"],
    )
    writer.writeheader()
    for row in results["data"]:
        writer.writerow({key: row.get(key) for key in writer.fieldnames})
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="task-{task_id}-results.csv"'},
    )


@app.post("/api/crawl/single")
async def single_lookup(payload: SingleLookup):
    phone = normalize_phone(payload.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    task = await task_manager.create_task(
        name=f"Single lookup {phone}",
        numbers=[phone],
        source_type="single",
        source_filename=None,
        speed="balanced",
        skip_checked=not payload.force,
        max_errors=0,
    )
    return {"success": True, "task": task}


@app.post("/api/crawl/range")
async def range_lookup(payload: RangeLookup):
    start = int(payload.start)
    end = int(payload.end)
    if end < start:
        raise HTTPException(status_code=400, detail="End must be greater than start")
    pad = len(payload.start)
    numbers = [f"{payload.prefix}{str(value).zfill(pad)}" for value in range(start, end + 1)]
    task = await task_manager.create_task(
        name=payload.name,
        numbers=numbers,
        source_type="range",
        source_filename=None,
        speed=payload.speed,
        skip_checked=payload.skip_checked,
        max_errors=payload.max_errors,
    )
    return {"success": True, "task": task}
