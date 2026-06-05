import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import ensure_runtime_dirs, get_settings


def _db_path() -> Path:
    ensure_runtime_dirs()
    return get_settings().path(get_settings().db_path)


@contextmanager
def connect():
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              source_type TEXT NOT NULL,
              source_filename TEXT,
              speed TEXT NOT NULL DEFAULT 'balanced',
              skip_checked INTEGER NOT NULL DEFAULT 1,
              status TEXT NOT NULL DEFAULT 'queued',
              current_phone TEXT,
              total_numbers INTEGER NOT NULL DEFAULT 0,
              completed_numbers INTEGER NOT NULL DEFAULT 0,
              success_count INTEGER NOT NULL DEFAULT 0,
              hidden_dp_count INTEGER NOT NULL DEFAULT 0,
              no_whatsapp_count INTEGER NOT NULL DEFAULT 0,
              error_count INTEGER NOT NULL DEFAULT 0,
              max_errors INTEGER NOT NULL DEFAULT 0,
              created_at TEXT DEFAULT (datetime('now')),
              started_at TEXT,
              finished_at TEXT,
              updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS task_numbers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id INTEGER NOT NULL,
              phone TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              position INTEGER NOT NULL DEFAULT 0,
              UNIQUE(task_id, phone),
              FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS results (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id INTEGER NOT NULL,
              phone TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              image_path TEXT,
              name TEXT,
              about TEXT,
              details_json TEXT,
              cover_image_path TEXT,
              error_msg TEXT,
              retries INTEGER NOT NULL DEFAULT 0,
              created_at TEXT DEFAULT (datetime('now')),
              updated_at TEXT DEFAULT (datetime('now')),
              UNIQUE(task_id, phone),
              FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_task_numbers_task_status ON task_numbers(task_id, status);
            CREATE INDEX IF NOT EXISTS idx_results_task_status ON results(task_id, status);
            CREATE INDEX IF NOT EXISTS idx_results_phone ON results(phone);
            """
        )
        _ensure_column(conn, "tasks", "max_errors", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "results", "details_json", "TEXT")
        _ensure_column(conn, "results", "cover_image_path", "TEXT")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {row["name"] for row in rows}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def create_task(
    name: str,
    numbers: list[str],
    source_type: str,
    source_filename: str | None,
    speed: str,
    skip_checked: bool,
    max_errors: int = 0,
) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks (name, source_type, source_filename, speed, skip_checked, total_numbers, max_errors)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, source_type, source_filename, speed, int(skip_checked), len(numbers), max_errors),
        )
        task_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT OR IGNORE INTO task_numbers (task_id, phone, position) VALUES (?, ?, ?)",
            [(task_id, phone, idx) for idx, phone in enumerate(numbers)],
        )
        return get_task(task_id, conn=conn)


def get_task(task_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    own = conn is None
    if own:
        ctx = connect()
        conn = ctx.__enter__()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row_to_dict(row)
    finally:
        if own:
            ctx.__exit__(None, None, None)


def list_tasks(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [row_to_dict(row) for row in rows if row]


def delete_task(task_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0


def get_task_image_paths(task_id: int) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT image_path, cover_image_path FROM results WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        paths: list[str] = []
        for row in rows:
            if row["image_path"]:
                paths.append(row["image_path"])
            if row["cover_image_path"]:
                paths.append(row["cover_image_path"])
        return paths


def update_task(task_id: int, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_task(task_id)
    fields["updated_at"] = "datetime('now')"
    assignments = []
    values = []
    for key, value in fields.items():
        if value == "datetime('now')":
            assignments.append(f"{key} = datetime('now')")
        else:
            assignments.append(f"{key} = ?")
            values.append(value)
    values.append(task_id)
    with connect() as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?", values)
        return get_task(task_id, conn=conn)


def next_pending_number(task_id: int) -> str | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT phone FROM task_numbers
            WHERE task_id = ? AND status = 'pending'
            ORDER BY position ASC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        return row["phone"] if row else None


def mark_task_number(task_id: int, phone: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE task_numbers SET status = ? WHERE task_id = ? AND phone = ?",
            (status, task_id, phone),
        )


def retry_error_numbers(task_id: int) -> int:
    with connect() as conn:
        rows = conn.execute(
            "SELECT phone FROM results WHERE task_id = ? AND status = 'error'",
            (task_id,),
        ).fetchall()
        phones = [row["phone"] for row in rows]
        if not phones:
            return 0
        conn.executemany(
            "UPDATE task_numbers SET status = 'pending' WHERE task_id = ? AND phone = ?",
            [(task_id, phone) for phone in phones],
        )
        conn.executemany(
            "UPDATE results SET status = 'pending', error_msg = NULL, updated_at = datetime('now') WHERE task_id = ? AND phone = ?",
            [(task_id, phone) for phone in phones],
        )
        return len(phones)


def upsert_result(
    task_id: int,
    phone: str,
    status: str,
    image_path: str | None = None,
    name: str | None = None,
    about: str | None = None,
    details: dict[str, Any] | None = None,
    cover_image_path: str | None = None,
    error_msg: str | None = None,
    retries: int = 0,
) -> dict[str, Any] | None:
    details_json = json.dumps(details or {}, ensure_ascii=False) if details else None
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO results (task_id, phone, status, image_path, name, about, details_json, cover_image_path, error_msg, retries, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(task_id, phone) DO UPDATE SET
              status = excluded.status,
              image_path = COALESCE(excluded.image_path, results.image_path),
              name = COALESCE(excluded.name, results.name),
              about = COALESCE(excluded.about, results.about),
              details_json = COALESCE(excluded.details_json, results.details_json),
              cover_image_path = COALESCE(excluded.cover_image_path, results.cover_image_path),
              error_msg = excluded.error_msg,
              retries = excluded.retries,
              updated_at = datetime('now')
            """,
            (task_id, phone, status, image_path, name, about, details_json, cover_image_path, error_msg, retries),
        )
        row = conn.execute(
            "SELECT * FROM results WHERE task_id = ? AND phone = ?",
            (task_id, phone),
        ).fetchone()
        return row_to_dict(row)


def get_result(task_id: int, phone: str) -> dict[str, Any] | None:
    with connect() as conn:
        return row_to_dict(
            conn.execute("SELECT * FROM results WHERE task_id = ? AND phone = ?", (task_id, phone)).fetchone()
        )


def get_latest_global_result(phone: str) -> dict[str, Any] | None:
    with connect() as conn:
        return row_to_dict(
            conn.execute(
                "SELECT * FROM results WHERE phone = ? ORDER BY updated_at DESC LIMIT 1",
                (phone,),
            ).fetchone()
        )


def recalc_task_counts(task_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        counts = {"success": 0, "hidden_dp": 0, "no_whatsapp": 0, "error": 0}
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM results WHERE task_id = ? GROUP BY status",
            (task_id,),
        ).fetchall()
        for row in rows:
            if row["status"] in counts:
                counts[row["status"]] = row["count"]
        completed = sum(counts.values())
        conn.execute(
            """
            UPDATE tasks SET
              completed_numbers = ?,
              success_count = ?,
              hidden_dp_count = ?,
              no_whatsapp_count = ?,
              error_count = ?,
              updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                completed,
                counts["success"],
                counts["hidden_dp"],
                counts["no_whatsapp"],
                counts["error"],
                task_id,
            ),
        )
        return get_task(task_id, conn=conn)


def get_results(task_id: int, page: int = 1, limit: int = 100, status: str = "", search: str = "") -> dict[str, Any]:
    offset = max(page - 1, 0) * limit
    conditions = ["task_id = ?"]
    params: list[Any] = [task_id]
    if status and status != "All":
        conditions.append("status = ?")
        params.append(status)
    if search:
        conditions.append("(phone LIKE ? OR name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    where = " AND ".join(conditions)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM results WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) AS count FROM results WHERE {where}", params).fetchone()["count"]
        return {"data": [row_to_dict(row) for row in rows], "total": total}


def get_stats() -> dict[str, Any]:
    with connect() as conn:
        status_rows = conn.execute("SELECT status, COUNT(*) AS count FROM results GROUP BY status").fetchall()
        task_rows = conn.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
        return {
            "results": {row["status"]: row["count"] for row in status_rows},
            "tasks": {row["status"]: row["count"] for row in task_rows},
        }
