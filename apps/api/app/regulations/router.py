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

ALLOWED_EXT = {".pdf", ".docx"}
MAX_BYTES = min(config.MAX_UPLOAD_BYTES, 50 * 1024 * 1024)

_SAFE_NAME = re.compile(r"[^\w.\-]+", re.UNICODE)
_jobs: dict[str, dict] = {}


def _err(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def _sanitize_filename(name: str) -> str:
    name = Path(name).name
    return _SAFE_NAME.sub("_", name)[:120] or "upload"


def _validate_signature(data: bytes, ext: str) -> None:
    head = data[:8]
    if ext == ".pdf" and not head.startswith(b"%PDF"):
        _err(422, "UNSUPPORTED_FORMAT", "File does not look like a PDF (no %PDF signature).")
    if ext == ".docx" and not head.startswith(b"PK"):
        _err(422, "UNSUPPORTED_FORMAT", "File does not look like a DOCX (not a zip archive).")


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
        _err(422, "UNSUPPORTED_FORMAT", f"Only .pdf/.docx supported, got '{ext}'.")
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
        # OCR explicitly out of scope: document stays UPLOADED
        doc["status"] = "UPLOADED"
    else:
        storage.save_fragments(doc_id, res.fragments)
        doc["fragmentCount"] = len(res.fragments)
        doc["status"] = "PARSED"
    storage.update_document(doc)
    return _doc_response(doc)


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
    llm = LocalLLM()
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
    llm = LocalLLM()
    if not await llm.is_reachable():
        _err(409, "LLM_UNAVAILABLE", "No local LLM reachable for verification.")
    cand = await verification.verify_candidate(cand, llm)
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
    return _cand_response(cand)


@router.post("/candidates/{cand_id}/activate")
def activate_candidate(cand_id: str):
    cand = _get_cand(cand_id)
    if cand["status"] != "VERIFIED":
        _err(409, "INVALID_TRANSITION",
             f"Only VERIFIED candidates can be activated (is {cand['status']}).")
    if cand["constraint"].get("value") is not None:
        # numeric rules require the deterministic literal gate to hold
        literal_ok = (cand.get("verification") or {}).get("numericLiteralFound")
        if literal_ok is None:
            literal_ok = extraction.numeric_gate(cand)
        if not literal_ok:
            _err(409, "NUMERIC_LITERAL_NOT_FOUND",
                 "Numeric value+unit not literally present in sourceText.")
    cand["status"] = "ACTIVE"
    storage.save_candidate(cand)
    return _cand_response(cand)


@router.post("/candidates/{cand_id}/deactivate")
def deactivate_candidate(cand_id: str):
    cand = _get_cand(cand_id)
    cand["status"] = "DEPRECATED"
    storage.save_candidate(cand)
    return _cand_response(cand)
