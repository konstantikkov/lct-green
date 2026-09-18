"""Persistence: SQLite for project/run metadata, JSON blobs on disk for
documents and iterations (data/projects/<id>/...). MVP-simple, reproducible.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import config

_lock = threading.Lock()


def _db_path() -> Path:
    return config.DATA_DIR / "greenplan.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    config.ensure_dirs()
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT, file_name TEXT, format TEXT,
            upload_path TEXT, dxf_path TEXT,
            state TEXT, file_hash TEXT,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, project_id TEXT,
            state TEXT, params_json TEXT, error TEXT,
            created_at REAL, finished_at REAL
        );
        """)


def project_dir(project_id: str) -> Path:
    d = config.DATA_DIR / "projects" / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def create_project(name: str, file_name: str, fmt: str, upload_path: str,
                   file_hash: str) -> str:
    pid = new_id("prj")
    with _lock, _conn() as c:
        c.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?)",
                  (pid, name, file_name, fmt, upload_path, "", "UPLOADED", file_hash, time.time()))
    return pid


def update_project(pid: str, **fields: Any) -> None:
    cols = {"dxf_path", "state", "format"}
    sets = {k: v for k, v in fields.items() if k in cols}
    if not sets:
        return
    q = "UPDATE projects SET " + ",".join(f"{k}=?" for k in sets) + " WHERE id=?"
    with _lock, _conn() as c:
        c.execute(q, (*sets.values(), pid))


def get_project(pid: str) -> Optional[dict]:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def list_projects() -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def create_run(pid: str, params: dict) -> str:
    rid = new_id("run")
    with _lock, _conn() as c:
        c.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
                  (rid, pid, "GENERATING_CANDIDATES", json.dumps(params), None, time.time(), None))
    return rid


def finish_run(rid: str, state: str, error: str | None = None) -> None:
    with _lock, _conn() as c:
        c.execute("UPDATE runs SET state=?, error=?, finished_at=? WHERE id=?",
                  (state, error, time.time(), rid))


# ---- JSON blob helpers -----------------------------------------------------

def save_json(pid: str, name: str, data: Any) -> Path:
    p = project_dir(pid) / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def load_json(pid: str, name: str) -> Any | None:
    p = project_dir(pid) / f"{name}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_iterations(pid: str) -> list[dict]:
    d = project_dir(pid)
    out = []
    for p in sorted(d.glob("iteration_*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        out.append(data)
    return sorted(out, key=lambda x: (x.get("runId", ""), x.get("index", 0)))
