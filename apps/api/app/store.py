"""Persistence layer.

Two interchangeable backends behind the same functions:
  * SQLite + JSON files on disk (default, zero-config local dev)
  * PostgreSQL when DATABASE_URL is set (e.g. Neon / AWS Lightsail managed DB):
    metadata in tables, large JSON/documents gzip-compressed in a blobs table,
    with a local disk cache in front (the DB may be cross-region).
"""
from __future__ import annotations

import gzip
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import config

_lock = threading.Lock()

USE_PG = bool(config.DATABASE_URL)
_pg_conn = None


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return config.DATA_DIR / "greenplan.db"


def _sqlite() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def _pg():
    """Single shared PG connection guarded by _lock; reconnects when broken."""
    global _pg_conn
    import psycopg
    if _pg_conn is None or _pg_conn.closed:
        _pg_conn = psycopg.connect(config.DATABASE_URL, autocommit=True,
                                   connect_timeout=20)
    return _pg_conn


def _pg_exec(query: str, params: tuple = (), fetch: str = ""):
    """Execute with one reconnect retry (long-lived conns get dropped)."""
    import psycopg
    global _pg_conn
    with _lock:
        for attempt in (0, 1):
            try:
                cur = _pg().execute(query, params)
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                return None
            except psycopg.OperationalError:
                if attempt:
                    raise
                try:
                    _pg_conn.close()
                except Exception:
                    pass
                _pg_conn = None


def init_db() -> None:
    config.ensure_dirs()
    if USE_PG:
        _pg_exec("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, name TEXT, file_name TEXT, format TEXT,
                upload_path TEXT, dxf_path TEXT, state TEXT, file_hash TEXT,
                created_at DOUBLE PRECISION)""")
        _pg_exec("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, project_id TEXT, state TEXT,
                params_json TEXT, error TEXT,
                created_at DOUBLE PRECISION, finished_at DOUBLE PRECISION)""")
        _pg_exec("""
            CREATE TABLE IF NOT EXISTS blobs (
                project_id TEXT, name TEXT, data BYTEA,
                updated_at DOUBLE PRECISION,
                PRIMARY KEY (project_id, name))""")
        return
    with _lock, _sqlite() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, name TEXT, file_name TEXT, format TEXT,
            upload_path TEXT, dxf_path TEXT, state TEXT, file_hash TEXT,
            created_at REAL);
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, project_id TEXT, state TEXT, params_json TEXT,
            error TEXT, created_at REAL, finished_at REAL);
        """)


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------

def project_dir(project_id: str) -> Path:
    d = config.DATA_DIR / "projects" / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


_PROJ_COLS = ("id", "name", "file_name", "format", "upload_path", "dxf_path",
              "state", "file_hash", "created_at")


def create_project(name: str, file_name: str, fmt: str, upload_path: str,
                   file_hash: str) -> str:
    pid = new_id("prj")
    row = (pid, name, file_name, fmt, upload_path, "", "UPLOADED", file_hash, time.time())
    if USE_PG:
        _pg_exec("INSERT INTO projects VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)
    else:
        with _lock, _sqlite() as c:
            c.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?)", row)
    return pid


def update_project(pid: str, **fields: Any) -> None:
    cols = {"dxf_path", "state", "format"}
    sets = {k: v for k, v in fields.items() if k in cols}
    if not sets:
        return
    if USE_PG:
        q = "UPDATE projects SET " + ",".join(f"{k}=%s" for k in sets) + " WHERE id=%s"
        _pg_exec(q, (*sets.values(), pid))
    else:
        q = "UPDATE projects SET " + ",".join(f"{k}=?" for k in sets) + " WHERE id=?"
        with _lock, _sqlite() as c:
            c.execute(q, (*sets.values(), pid))


def get_project(pid: str) -> Optional[dict]:
    if USE_PG:
        row = _pg_exec("SELECT * FROM projects WHERE id=%s", (pid,), fetch="one")
        return dict(zip(_PROJ_COLS, row)) if row else None
    with _lock, _sqlite() as c:
        row = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def list_projects() -> list[dict]:
    if USE_PG:
        rows = _pg_exec("SELECT * FROM projects ORDER BY created_at DESC", fetch="all") or []
        return [dict(zip(_PROJ_COLS, r)) for r in rows]
    with _lock, _sqlite() as c:
        rows = c.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def create_run(pid: str, params: dict) -> str:
    rid = new_id("run")
    row = (rid, pid, "GENERATING_CANDIDATES", json.dumps(params), None, time.time(), None)
    if USE_PG:
        _pg_exec("INSERT INTO runs VALUES (%s,%s,%s,%s,%s,%s,%s)", row)
    else:
        with _lock, _sqlite() as c:
            c.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)", row)
    return rid


def finish_run(rid: str, state: str, error: str | None = None) -> None:
    if USE_PG:
        _pg_exec("UPDATE runs SET state=%s, error=%s, finished_at=%s WHERE id=%s",
                 (state, error, time.time(), rid))
    else:
        with _lock, _sqlite() as c:
            c.execute("UPDATE runs SET state=?, error=?, finished_at=? WHERE id=?",
                      (state, error, time.time(), rid))


# ---------------------------------------------------------------------------
# blobs (JSON documents, iterations, source DXF)
# ---------------------------------------------------------------------------

def _cache_path(pid: str, name: str) -> Path:
    return project_dir(pid) / f"{name}.json"


def save_json(pid: str, name: str, data: Any) -> Path:
    raw = json.dumps(data, ensure_ascii=False)
    p = _cache_path(pid, name)
    p.write_text(raw, encoding="utf-8")
    if USE_PG:
        _pg_exec("""INSERT INTO blobs VALUES (%s,%s,%s,%s)
                    ON CONFLICT (project_id, name)
                    DO UPDATE SET data=EXCLUDED.data, updated_at=EXCLUDED.updated_at""",
                 (pid, name, gzip.compress(raw.encode("utf-8"), 6), time.time()))
    return p


def load_json(pid: str, name: str) -> Any | None:
    p = _cache_path(pid, name)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    if USE_PG:
        row = _pg_exec("SELECT data FROM blobs WHERE project_id=%s AND name=%s",
                       (pid, name), fetch="one")
        if row:
            raw = gzip.decompress(bytes(row[0])).decode("utf-8")
            p.write_text(raw, encoding="utf-8")  # refill local cache
            return json.loads(raw)
    return None


def save_file_blob(pid: str, name: str, path: Path) -> None:
    """Persist a source file (e.g. converted DXF) for disk-loss durability."""
    if not USE_PG:
        return
    _pg_exec("""INSERT INTO blobs VALUES (%s,%s,%s,%s)
                ON CONFLICT (project_id, name)
                DO UPDATE SET data=EXCLUDED.data, updated_at=EXCLUDED.updated_at""",
             (pid, name, gzip.compress(path.read_bytes(), 6), time.time()))


def restore_file_blob(pid: str, name: str, target: Path) -> bool:
    if not USE_PG:
        return False
    row = _pg_exec("SELECT data FROM blobs WHERE project_id=%s AND name=%s",
                   (pid, name), fetch="one")
    if not row:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.decompress(bytes(row[0])))
    return True


def list_iterations(pid: str) -> list[dict]:
    if USE_PG:
        rows = _pg_exec(
            "SELECT data FROM blobs WHERE project_id=%s AND name LIKE 'iteration_%%'",
            (pid,), fetch="all") or []
        out = [json.loads(gzip.decompress(bytes(r[0])).decode("utf-8")) for r in rows]
    else:
        d = project_dir(pid)
        out = [json.loads(p.read_text(encoding="utf-8"))
               for p in sorted(d.glob("iteration_*.json"))]
    return sorted(out, key=lambda x: (x.get("runId", ""), x.get("index", 0)))


def delete_project(pid: str) -> bool:
    """Removes a project: rows, blobs, disk cache, uploaded files."""
    import shutil
    proj = get_project(pid)
    if proj is None:
        return False
    if USE_PG:
        _pg_exec("DELETE FROM blobs WHERE project_id=%s", (pid,))
        _pg_exec("DELETE FROM runs WHERE project_id=%s", (pid,))
        _pg_exec("DELETE FROM projects WHERE id=%s", (pid,))
    else:
        with _lock, _sqlite() as c:
            c.execute("DELETE FROM runs WHERE project_id=?", (pid,))
            c.execute("DELETE FROM projects WHERE id=?", (pid,))
    shutil.rmtree(config.DATA_DIR / "projects" / pid, ignore_errors=True)
    for key in ("upload_path", "dxf_path"):
        p = proj.get(key)
        if p:
            Path(p).unlink(missing_ok=True)
    return True
