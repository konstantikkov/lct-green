"""HTTP endpoints for the regulations feature — FROZEN contract
(docs/API_CONTRACT.md § Regulations feature WS4), prefix /api/regulations."""
from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import config
from ..services.llm import LocalLLM
from . import docparse, extraction, storage, verification
from .models import Fragment, RegDocument, RuleCandidate

router = APIRouter(prefix="/api/regulations", tags=["regulations"])

ALLOWED_EXT = {".pdf", ".docx", ".rtf"}
MAX_BYTES = min(config.MAX_UPLOAD_BYTES, 50 * 1024 * 1024)

_SAFE_NAME = re.compile(r"[^\w.\-]+", re.UNICODE)
_jobs: dict[str, dict] = {}


def _err(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


MAX_NAME_LEN = 120


def _sanitize_filename(name: str) -> str:
    """Strip the path, replace unsafe characters, shorten — but never lose the
    extension: the caller validates the format by suffix, and real normative
    files have names long enough to be truncated past it, e.g.
    "О мерах по регулированию ... от 03 марта 2026 - docs.cntd.ru.pdf" (138
    chars) used to come out extensionless and get rejected as unsupported.
    """
    p = Path(Path(name).name)
    suffix = _SAFE_NAME.sub("_", p.suffix)[:16]
    stem = _SAFE_NAME.sub("_", p.stem)[:MAX_NAME_LEN - len(suffix)]
    return (stem + suffix) or "upload"


def _validate_signature(data: bytes, ext: str) -> None:
    head = data[:8]
    if ext == ".pdf" and not head.startswith(b"%PDF"):
        _err(422, "UNSUPPORTED_FORMAT", "File does not look like a PDF (no %PDF signature).")
    if ext == ".docx" and not head.startswith(b"PK"):
        _err(422, "UNSUPPORTED_FORMAT", "File does not look like a DOCX (not a zip archive).")
    if ext == ".rtf" and not head.startswith(b"{\\rtf"):
        _err(422, "UNSUPPORTED_FORMAT", "File does not look like an RTF (no {\\rtf signature).")


def _doc_response(doc: dict) -> dict:
    doc = dict(doc)
    doc["candidateCounts"] = storage.candidate_counts(doc["id"])
    doc["fragmentCount"] = doc.get("fragmentCount", 0)
    return RegDocument.model_validate(doc).model_dump()


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

@router.post("/documents", status_code=201)
async def upload_document(file: UploadFile = File(...)):
    fname = _sanitize_filename(file.filename or "upload")
    ext = Path(fname).suffix.lower()
    if ext not in ALLOWED_EXT:
        _err(422, "UNSUPPORTED_FORMAT", f"Only .pdf/.docx/.rtf supported, got '{ext}'.")
    data = await file.read()
    if len(data) > MAX_BYTES:
        _err(413, "FILE_TOO_LARGE", "Upload exceeds size limit.")
    if not data:
        _err(422, "UNSUPPORTED_FORMAT", "Empty file.")
    _validate_signature(data, ext)

    doc_id = storage.new_id("regdoc")
    path = storage.save_original(doc_id, ext, data)
    doc = {
        "id": doc_id, "fileName": fname, "title": None, "documentNumber": None,
        "jurisdiction": None, "publicationDate": None, "effectiveDate": None,
        "version": "1", "fileHash": hashlib.sha256(data).hexdigest(),
        "status": "UPLOADED", "uploadedAt": time.time(), "pageCount": None,
        "fragmentCount": 0, "parseLog": [f"stored original at {path}"],
        "originalPath": str(path),
    }
    storage.create_document(doc)

    try:
        res = await asyncio.to_thread(docparse.parse_document, path, doc_id, ext)
    except docparse.ParseError as exc:
        doc["status"] = "FAILED"
        doc["parseLog"].append(str(exc))
        storage.update_document(doc)
        _err(422, "PARSE_FAILED", str(exc))

    doc["title"] = res.title
    doc["documentNumber"] = res.document_number
    doc["pageCount"] = res.page_count
    doc["parseLog"].extend(res.parse_log)
    if res.needs_ocr:
        doc["status"] = "UPLOADED"
        if docparse.ocr_available():
            # scanned PDF: recognize in the background (minutes for large
            # documents); the UI polls document status until PARSED
            doc["parseLog"].append(f"OCR started ({doc.get('pageCount') or '?'} pages)")
            t = threading.Thread(target=_run_ocr_job,
                                 args=(doc_id, path, doc.get("pageCount") or 0),
                                 daemon=True)
            t.start()
    else:
        storage.save_fragments(doc_id, res.fragments)
        doc["fragmentCount"] = len(res.fragments)
        doc["status"] = "PARSED"
    storage.update_document(doc)
    return _doc_response(doc)


def _run_ocr_job(doc_id: str, path: Path, page_count: int) -> None:
    def _progress(page: int, total: int) -> None:
        if page % 10 == 0 or page == total:
            d = storage.get_document(doc_id)
            if d:
                d["parseLog"] = [ln for ln in d.get("parseLog", [])
                                 if not ln.startswith("OCR progress:")]
                d["parseLog"].append(f"OCR progress: {page}/{total} pages")
                storage.update_document(d)

    try:
        res = docparse.parse_pdf_ocr(path, doc_id, page_count, progress=_progress)
        doc = storage.get_document(doc_id)
        if not doc:
            return
        storage.save_fragments(doc_id, res.fragments)
        doc["fragmentCount"] = len(res.fragments)
        doc["title"] = doc.get("title") or res.title
        doc["documentNumber"] = doc.get("documentNumber") or res.document_number
        doc["parseLog"] = [ln for ln in doc.get("parseLog", [])
                           if not ln.startswith("OCR progress:")]
        doc["parseLog"].extend(res.parse_log)
        doc["status"] = "PARSED"
        storage.update_document(doc)
    except Exception as exc:  # noqa: BLE001
        doc = storage.get_document(doc_id)
        if doc:
            doc["parseLog"].append(f"OCR failed: {exc}")
            storage.update_document(doc)


@router.get("/documents")
def list_documents():
    return [_doc_response(d) for d in storage.list_documents()]


@router.get("/documents/{doc_id}")
def get_document(doc_id: str):
    doc = storage.get_document(doc_id)
    if doc is None:
        _err(404, "DOCUMENT_NOT_FOUND", f"No document {doc_id}")
    return _doc_response(doc)


@router.get("/documents/{doc_id}/fragments")
def get_fragments(doc_id: str, path: Optional[str] = None,
                  offset: int = 0, limit: int = 200):
    if storage.get_document(doc_id) is None:
        _err(404, "DOCUMENT_NOT_FOUND", f"No document {doc_id}")
    frags = storage.list_fragments(doc_id, path_prefix=path,
                                   offset=max(0, offset), limit=max(1, min(limit, 1000)))
    return [Fragment.model_validate(f).model_dump() for f in frags]


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

def _run_extraction_job(job_id: str, doc_id: str, llm) -> None:
    try:
        summary = asyncio.run(extraction.extract_document(doc_id, llm))
        _jobs[job_id].update(state="COMPLETED", summary=summary)
    except Exception as exc:  # noqa: BLE001
        _jobs[job_id].update(state="FAILED", error=str(exc))
        doc = storage.get_document(doc_id)
        if doc:
            doc.setdefault("parseLog", []).append(f"extraction failed: {exc}")
            storage.update_document(doc)


@router.post("/documents/{doc_id}/extract", status_code=202)
async def extract_document(doc_id: str):
    doc = storage.get_document(doc_id)
    if doc is None:
        _err(404, "DOCUMENT_NOT_FOUND", f"No document {doc_id}")
    if doc.get("status") not in ("PARSED", "EXTRACTED"):
        _err(409, "NOT_PARSED", f"Document is {doc.get('status')}; no parsed fragments to extract from.")
    llm = extraction.reg_llm()
    if not await llm.is_reachable():
        _err(409, "LLM_UNAVAILABLE", "No local LLM reachable for extraction.")
    job_id = f"regjob_{uuid.uuid4().hex[:10]}"
    _jobs[job_id] = {"jobId": job_id, "documentId": doc_id, "state": "RUNNING"}
    t = threading.Thread(target=_run_extraction_job, args=(job_id, doc_id, llm),
                         daemon=True)
    t.start()
    return {"jobId": job_id}


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------

def _cand_response(cand: dict) -> dict:
    return RuleCandidate.model_validate(cand).model_dump()


@router.get("/candidates")
def list_candidates(documentId: Optional[str] = None, status: Optional[str] = None):
    return [_cand_response(c)
            for c in storage.list_candidates(document_id=documentId, status=status)]


def _get_cand(cand_id: str) -> dict:
    cand = storage.get_candidate(cand_id)
    if cand is None:
        _err(404, "CANDIDATE_NOT_FOUND", f"No candidate {cand_id}")
    return cand


@router.post("/candidates/{cand_id}/verify")
async def verify_candidate(cand_id: str):
    cand = _get_cand(cand_id)
    llm = extraction.reg_llm()
    if not await llm.is_reachable():
        _err(409, "LLM_UNAVAILABLE", "No local LLM reachable for verification.")
    cand = await verification.verify_candidate(cand, llm)
    invalidate_project_caches()
    return _cand_response(cand)


class CandidateEdits(BaseModel):
    constraint: Optional[dict] = None
    applicability: Optional[dict] = None


class CandidatePatch(BaseModel):
    action: str  # approve | reject | edit
    edits: Optional[CandidateEdits] = None
    reviewer: Optional[str] = None


@router.patch("/candidates/{cand_id}")
def patch_candidate(cand_id: str, patch: CandidatePatch):
    cand = _get_cand(cand_id)
    if patch.action not in ("approve", "reject", "edit"):
        _err(422, "INVALID_ACTION", f"Unknown action '{patch.action}'")

    def _apply_edits() -> None:
        # every edit bumps ruleVersion; the old version is retained
        storage.supersede_candidate(cand)
        cand["ruleVersion"] = int(cand.get("ruleVersion", 1)) + 1
        if patch.edits.constraint:
            cand["constraint"].update(patch.edits.constraint)
        if patch.edits.applicability:
            cand["applicability"].update(patch.edits.applicability)
        # deterministic gate re-applied on the edited values
        literal_ok = extraction.numeric_gate(cand)
        if cand.get("verification"):
            cand["verification"]["numericLiteralFound"] = literal_ok
        if not literal_ok:
            cand["machineExecutable"] = False
            cand["status"] = "NEEDS_REVIEW"

    if patch.edits and (patch.edits.constraint or patch.edits.applicability):
        _apply_edits()

    if patch.action == "approve":
        if cand["status"] not in ("NEEDS_REVIEW", "EXTRACTED", "VERIFIED"):
            _err(409, "INVALID_TRANSITION",
                 f"Cannot approve candidate in status {cand['status']}")
        cand["status"] = "VERIFIED"
    elif patch.action == "reject":
        cand["status"] = "REJECTED"

    cand["reviewer"] = patch.reviewer or cand.get("reviewer")
    cand["reviewDate"] = time.time()
    storage.save_candidate(cand)
    invalidate_project_caches()
    return _cand_response(cand)


@router.post("/candidates/{cand_id}/activate")
def activate_candidate(cand_id: str):
    cand = _get_cand(cand_id)
    if cand["status"] != "VERIFIED":
        _err(409, "INVALID_TRANSITION",
             f"Only VERIFIED candidates can be activated (is {cand['status']}).")
    if cand["constraint"].get("value") is not None:
        # numeric rules require the deterministic literal gate — OR an explicit
        # named human review. OCR'd tables often carry the unit only in the
        # header row; the reviewer sees the source fragment and is the
        # authority the review flow exists for. The override is recorded.
        literal_ok = (cand.get("verification") or {}).get("numericLiteralFound")
        if literal_ok is None:
            literal_ok = extraction.numeric_gate(cand)
        if not literal_ok:
            if not cand.get("reviewer"):
                _err(409, "NUMERIC_LITERAL_NOT_FOUND",
                     "Numeric value+unit not literally present in sourceText "
                     "and no named human review — cannot activate automatically.")
            ver = cand.get("verification") or {}
            note = ("activated on human review by "
                    f"'{cand['reviewer']}' despite missing literal unit in the "
                    "cited fragment (OCR table)")
            ver["notes"] = f"{ver.get('notes') or ''}; {note}".strip("; ")
            cand["verification"] = ver
    cand["status"] = "ACTIVE"
    storage.save_candidate(cand)
    invalidate_project_caches()
    return _cand_response(cand)


@router.post("/candidates/{cand_id}/deactivate")
def deactivate_candidate(cand_id: str):
    cand = _get_cand(cand_id)
    cand["status"] = "DEPRECATED"
    storage.save_candidate(cand)
    invalidate_project_caches()
    return _cand_response(cand)


# ---------------------------------------------------------------------------
# free-text conditions (wizard: "условия" step) — user-entered text goes
# through the SAME extraction + gates + review flow as uploaded documents
# ---------------------------------------------------------------------------

class TextCondition(BaseModel):
    text: str
    title: Optional[str] = None


def invalidate_project_caches() -> None:
    """Rules changed (activation/edit): cached constraints must be rebuilt."""
    try:
        from .. import pipeline
        for rt in list(pipeline._runtimes.values()):
            rt.invalidate_semantics()
    except Exception:
        pass


@router.post("/text", status_code=201)
async def add_text_condition(body: TextCondition):
    text = (body.text or "").strip()
    if len(text) < 10:
        _err(422, "EMPTY_TEXT", "Введите текст условия (минимум 10 символов).")
    if len(text) > 20000:
        _err(413, "TEXT_TOO_LARGE", "Текст условия слишком большой (лимит 20 000 символов).")
    llm = extraction.reg_llm()
    if not await llm.is_reachable():
        _err(409, "LLM_UNAVAILABLE", "Локальная LLM недоступна для разбора условия.")

    doc_id = storage.new_id("regtxt")
    data = text.encode("utf-8")
    path = storage.save_original(doc_id, ".txt", data)
    title = (body.title or "Пользовательское условие").strip()[:120]
    doc = {
        "id": doc_id, "fileName": f"{title}.txt", "title": title,
        "documentNumber": None, "jurisdiction": None, "publicationDate": None,
        "effectiveDate": None, "version": "1",
        "fileHash": hashlib.sha256(data).hexdigest(),
        "status": "PARSED", "uploadedAt": time.time(), "pageCount": None,
        "fragmentCount": 0,
        "parseLog": [f"user-entered text condition stored at {path}"],
        "originalPath": str(path),
    }
    storage.create_document(doc)
    frags = []
    for i, para in enumerate(p.strip() for p in text.splitlines() if p.strip()):
        frags.append({"id": f"{doc_id}:text:p{i + 1}", "page": None,
                      "hierarchyPath": f"текст > абзац {i + 1}", "kind": "clause",
                      "text": para, "order": i + 1})
    storage.save_fragments(doc_id, frags)
    doc["fragmentCount"] = len(frags)
    storage.update_document(doc)

    # text conditions are small — extract synchronously so the wizard can show
    # candidates for review immediately
    try:
        await extraction.extract_document(doc_id, llm)
    except Exception as exc:  # noqa: BLE001
        doc["parseLog"].append(f"extraction failed: {exc}")
        storage.update_document(doc)
        _err(502, "EXTRACTION_FAILED", f"LLM не смогла разобрать условие: {exc}")

    return {
        "document": _doc_response(storage.get_document(doc_id)),
        "candidates": [_cand_response(c) for c in storage.list_candidates(document_id=doc_id)],
    }
