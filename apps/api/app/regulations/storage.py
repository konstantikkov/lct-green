"""Persistence for the regulations feature.

Same dual-backend approach as app.store: SQLite tables inside the existing
greenplan.db by default, PostgreSQL (via app.store's connection helpers) when
DATABASE_URL is set. Documents/fragments/candidates are stored as JSON rows
with a few indexed columns; original uploads live on disk under
data/regulations/<docId>/original.<ext> (path recorded, gzip blob mirrored to
the DB blobs table when Postgres is available).

Versioning: every superseded candidate version is retained in
reg_candidate_versions before the live row in reg_candidates is overwritten.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .. import config
from .. import store as base_store
from .models import CANDIDATE_STATUSES

_lock = threading.Lock()
_initialized = False


def _use_pg() -> bool:
    return bool(config.DATABASE_URL)


def _sqlite() -> sqlite3.Connection:
    c = sqlite3.connect(config.DATA_DIR / "greenplan.db")
    c.row_factory = sqlite3.Row
    return c


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def init_reg_db() -> None:
    global _initialized
    config.ensure_dirs()
    (config.DATA_DIR / "regulations").mkdir(parents=True, exist_ok=True)
    if _use_pg():
        base_store._pg_exec("""
            CREATE TABLE IF NOT EXISTS reg_documents (
                id TEXT PRIMARY KEY, status TEXT, uploaded_at DOUBLE PRECISION,
                data TEXT)""")
        base_store._pg_exec("""
            CREATE TABLE IF NOT EXISTS reg_fragments (
                document_id TEXT, id TEXT, ord INTEGER, data TEXT,
                PRIMARY KEY (document_id, id))""")
        base_store._pg_exec("""
            CREATE TABLE IF NOT EXISTS reg_candidates (
                id TEXT PRIMARY KEY, document_id TEXT, status TEXT,
                updated_at DOUBLE PRECISION, data TEXT)""")
        base_store._pg_exec("""
            CREATE TABLE IF NOT EXISTS reg_candidate_versions (
                candidate_id TEXT, rule_version INTEGER,
                superseded_at DOUBLE PRECISION, data TEXT,
                PRIMARY KEY (candidate_id, rule_version))""")
    else:
        with _lock, _sqlite() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS reg_documents (
                id TEXT PRIMARY KEY, status TEXT, uploaded_at REAL, data TEXT);
            CREATE TABLE IF NOT EXISTS reg_fragments (
                document_id TEXT, id TEXT, ord INTEGER, data TEXT,
                PRIMARY KEY (document_id, id));
            CREATE TABLE IF NOT EXISTS reg_candidates (
                id TEXT PRIMARY KEY, document_id TEXT, status TEXT,
                updated_at REAL, data TEXT);
            CREATE TABLE IF NOT EXISTS reg_candidate_versions (
                candidate_id TEXT, rule_version INTEGER, superseded_at REAL,
                data TEXT, PRIMARY KEY (candidate_id, rule_version));
            """)
    _initialized = True


def _ensure() -> None:
    if not _initialized:
        init_reg_db()


# ---------------------------------------------------------------------------
# original files
# ---------------------------------------------------------------------------

def document_dir(doc_id: str) -> Path:
    d = config.DATA_DIR / "regulations" / doc_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_original(doc_id: str, ext: str, data: bytes) -> Path:
    """Store the original upload bytes on disk (+ DB blob when Postgres)."""
    path = document_dir(doc_id) / f"original{ext}"
    path.write_bytes(data)
    try:
        base_store.save_file_blob(doc_id, "reg_original", path)
    except Exception:
        pass  # durability extra; disk copy remains authoritative
    return path


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def create_document(doc: dict) -> None:
    _ensure()
    row = (doc["id"], doc.get("status", "UPLOADED"), doc.get("uploadedAt", time.time()),
           json.dumps(doc, ensure_ascii=False))
    if _use_pg():
        base_store._pg_exec("INSERT INTO reg_documents VALUES (%s,%s,%s,%s)", row)
    else:
        with _lock, _sqlite() as c:
            c.execute("INSERT INTO reg_documents VALUES (?,?,?,?)", row)


def update_document(doc: dict) -> None:
    _ensure()
    row = (doc.get("status", "UPLOADED"), json.dumps(doc, ensure_ascii=False), doc["id"])
    if _use_pg():
        base_store._pg_exec(
            "UPDATE reg_documents SET status=%s, data=%s WHERE id=%s", row)
    else:
        with _lock, _sqlite() as c:
            c.execute("UPDATE reg_documents SET status=?, data=? WHERE id=?", row)


def get_document(doc_id: str) -> Optional[dict]:
    _ensure()
    if _use_pg():
        r = base_store._pg_exec("SELECT data FROM reg_documents WHERE id=%s",
                                (doc_id,), fetch="one")
        return json.loads(r[0]) if r else None
    with _lock, _sqlite() as c:
        r = c.execute("SELECT data FROM reg_documents WHERE id=?", (doc_id,)).fetchone()
    return json.loads(r["data"]) if r else None


def list_documents() -> list[dict]:
    _ensure()
    if _use_pg():
        rows = base_store._pg_exec(
            "SELECT data FROM reg_documents ORDER BY uploaded_at DESC", fetch="all") or []
        return [json.loads(r[0]) for r in rows]
    with _lock, _sqlite() as c:
        rows = c.execute("SELECT data FROM reg_documents ORDER BY uploaded_at DESC").fetchall()
    return [json.loads(r["data"]) for r in rows]


def find_document_by_hash(file_hash: str) -> Optional[dict]:
    for d in list_documents():
        if d.get("fileHash") == file_hash:
            return d
    return None


# ---------------------------------------------------------------------------
# fragments
# ---------------------------------------------------------------------------

def save_fragments(doc_id: str, fragments: list[dict]) -> None:
    _ensure()
    if _use_pg():
        base_store._pg_exec("DELETE FROM reg_fragments WHERE document_id=%s", (doc_id,))
        for f in fragments:
            base_store._pg_exec(
                "INSERT INTO reg_fragments VALUES (%s,%s,%s,%s)",
                (doc_id, f["id"], f.get("order", 0), json.dumps(f, ensure_ascii=False)))
        return
    with _lock, _sqlite() as c:
        c.execute("DELETE FROM reg_fragments WHERE document_id=?", (doc_id,))
        c.executemany(
            "INSERT INTO reg_fragments VALUES (?,?,?,?)",
            [(doc_id, f["id"], f.get("order", 0), json.dumps(f, ensure_ascii=False))
             for f in fragments])


def list_fragments(doc_id: str, path_prefix: str | None = None,
                   offset: int = 0, limit: int = 200) -> list[dict]:
    frags = _all_fragments(doc_id)
    if path_prefix:
        frags = [f for f in frags if f.get("hierarchyPath", "").startswith(path_prefix)]
    return frags[offset:offset + limit]


def _all_fragments(doc_id: str) -> list[dict]:
    _ensure()
    if _use_pg():
        rows = base_store._pg_exec(
            "SELECT data FROM reg_fragments WHERE document_id=%s ORDER BY ord",
            (doc_id,), fetch="all") or []
        return [json.loads(r[0]) for r in rows]
    with _lock, _sqlite() as c:
        rows = c.execute(
            "SELECT data FROM reg_fragments WHERE document_id=? ORDER BY ord",
            (doc_id,)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def fragments_by_ids(doc_id: str, ids: list[str]) -> dict[str, dict]:
    wanted = set(ids)
    return {f["id"]: f for f in _all_fragments(doc_id) if f["id"] in wanted}


# ---------------------------------------------------------------------------
# candidates (+ retained versions)
# ---------------------------------------------------------------------------

def save_candidate(cand: dict) -> None:
    """Insert or update the live candidate row (no version bump)."""
    _ensure()
    payload = json.dumps(cand, ensure_ascii=False)
    now = time.time()
    if _use_pg():
        base_store._pg_exec("""
            INSERT INTO reg_candidates VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,
                updated_at=EXCLUDED.updated_at, data=EXCLUDED.data""",
            (cand["id"], cand["documentId"], cand["status"], now, payload))
        return
    with _lock, _sqlite() as c:
        c.execute("""INSERT INTO reg_candidates VALUES (?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                       updated_at=excluded.updated_at, data=excluded.data""",
                  (cand["id"], cand["documentId"], cand["status"], now, payload))


def supersede_candidate(old: dict) -> None:
    """Retain a superseded candidate version before overwriting the live row."""
    _ensure()
    row = (old["id"], old.get("ruleVersion", 1), time.time(),
           json.dumps(old, ensure_ascii=False))
    if _use_pg():
        base_store._pg_exec("""
            INSERT INTO reg_candidate_versions VALUES (%s,%s,%s,%s)
            ON CONFLICT (candidate_id, rule_version) DO NOTHING""", row)
        return
    with _lock, _sqlite() as c:
        c.execute("INSERT OR IGNORE INTO reg_candidate_versions VALUES (?,?,?,?)", row)


def get_candidate(cand_id: str) -> Optional[dict]:
    _ensure()
    if _use_pg():
        r = base_store._pg_exec("SELECT data FROM reg_candidates WHERE id=%s",
                                (cand_id,), fetch="one")
        return json.loads(r[0]) if r else None
    with _lock, _sqlite() as c:
        r = c.execute("SELECT data FROM reg_candidates WHERE id=?", (cand_id,)).fetchone()
    return json.loads(r["data"]) if r else None


def list_candidates(document_id: str | None = None,
                    status: str | None = None) -> list[dict]:
    _ensure()
    conds, params = [], []
    if document_id:
        conds.append("document_id=");  params.append(document_id)
    if status:
        conds.append("status=");  params.append(status)
    if _use_pg():
        q = "SELECT data FROM reg_candidates"
        if conds:
            q += " WHERE " + " AND ".join(c + "%s" for c in conds)
        rows = base_store._pg_exec(q + " ORDER BY updated_at", tuple(params),
                                   fetch="all") or []
        return [json.loads(r[0]) for r in rows]
    q = "SELECT data FROM reg_candidates"
    if conds:
        q += " WHERE " + " AND ".join(c + "?" for c in conds)
    with _lock, _sqlite() as c:
        rows = c.execute(q + " ORDER BY updated_at", tuple(params)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def list_candidate_versions(cand_id: str) -> list[dict]:
    _ensure()
    if _use_pg():
        rows = base_store._pg_exec(
            "SELECT data FROM reg_candidate_versions WHERE candidate_id=%s "
            "ORDER BY rule_version", (cand_id,), fetch="all") or []
        return [json.loads(r[0]) for r in rows]
    with _lock, _sqlite() as c:
        rows = c.execute(
            "SELECT data FROM reg_candidate_versions WHERE candidate_id=? "
            "ORDER BY rule_version", (cand_id,)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def candidate_counts(doc_id: str) -> dict[str, int]:
    counts = {s: 0 for s in CANDIDATE_STATUSES}
    for c in list_candidates(document_id=doc_id):
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return counts
