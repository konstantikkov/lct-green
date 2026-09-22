"""Document parsing: PDF (pypdf) and DOCX (python-docx) → hierarchy fragments.

Fragments get STABLE ids `<docKey>:<h1_slug>[:<h2_slug>]:p<order>` (t<order>
for tables) so re-parsing the same file yields identical ids. docKey comes
from the document number when detectable in the first pages (СП …, ГОСТ …,
ППМ …), else from the document-id prefix.

OCR is explicitly OUT OF SCOPE: a scanned PDF with no extractable text keeps
status UPLOADED and the parseLog says OCR would be required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CLAUSE_RE = re.compile(r"^(\d+(?:\.\d+)+)[.)]?\s+")
DOCNUM_RE = re.compile(
    r"(СП\s*[\d.]+\d|ГОСТ\s*[\d.\-]+\d|СНиП\s*[\d.\-]+\d|"
    r"ППМ\s*№?\s*[\d\-]+(?:-ПП)?|№\s*\d+-ПП)", re.IGNORECASE)
NOTE_RE = re.compile(r"^(примечани|прим\.)", re.IGNORECASE)
HEADING_STYLE_RE = re.compile(r"^(?:Heading|Заголовок)\s*(\d)", re.IGNORECASE)

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


class ParseError(Exception):
    pass


def slugify(text: str, max_len: int = 24) -> str:
    out = []
    for ch in text.lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() or ch == ".":
            out.append(ch)
        else:
            out.append("-")
    slug = re.sub(r"-{2,}", "-", "".join(out)).strip("-.")
    return slug[:max_len].strip("-.") or "x"


def detect_doc_number(texts: list[str]) -> str | None:
    """Scan the first pages/paragraphs for a normative document number."""
    for t in texts[:60]:
        m = DOCNUM_RE.search(t or "")
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    return None


@dataclass
class ParseResult:
    fragments: list[dict] = field(default_factory=list)
    title: str | None = None
    document_number: str | None = None
    page_count: int | None = None
    parse_log: list[str] = field(default_factory=list)
    needs_ocr: bool = False


class _Builder:
    """Shared fragment builder maintaining heading stack + global order."""

    def __init__(self, doc_key_fallback: str):
        self.doc_key_fallback = doc_key_fallback
        self.doc_key: str | None = None
        self.headings: list[tuple[int, str]] = []  # (level, title)
        self.fragments: list[dict] = []
        self.order = 0

    def _key(self) -> str:
        return slugify(self.doc_key, 32) if self.doc_key else self.doc_key_fallback

    def _slug_trail(self) -> str:
        parts = [slugify(t) for _, t in self.headings[:2]]  # h1 (+ h2) slugs
        return ":".join(parts) if parts else "root"

    def _heading_path(self) -> str:
        return " > ".join(t for _, t in self.headings)

    def push_heading(self, level: int, text: str, page: int | None) -> None:
        self.headings = [h for h in self.headings if h[0] < level]
        self.headings.append((level, text))
        self.order += 1
        self.fragments.append({
            "id": f"{self._key()}:{self._slug_trail()}:p{self.order}",
            "page": page, "hierarchyPath": self._heading_path(),
            "kind": "heading", "text": text, "order": self.order,
        })

    def push_paragraph(self, text: str, page: int | None) -> None:
        self.order += 1
        kind = "note" if NOTE_RE.match(text) else "clause" if CLAUSE_RE.match(text) else "clause"
        m = CLAUSE_RE.match(text)
        if m:
            num = m.group(1)
            parts = num.split(".")
            path = " > ".join(".".join(parts[:i + 1]) for i in range(len(parts)))
        else:
            path = self._heading_path()
        if NOTE_RE.match(text):
            kind = "note"
        self.fragments.append({
            "id": f"{self._key()}:{self._slug_trail()}:p{self.order}",
            "page": page, "hierarchyPath": path,
            "kind": kind, "text": text, "order": self.order,
        })

    def push_table(self, headers: list[str], rows: list[list[str]],
                   page: int | None) -> None:
        self.order += 1
        lines = [" | ".join(headers)] + [" | ".join(r) for r in rows]
        self.fragments.append({
            "id": f"{self._key()}:{self._slug_trail()}:t{self.order}",
            "page": page, "hierarchyPath": self._heading_path() or "Таблица",
            "kind": "table", "text": "\n".join(lines), "order": self.order,
            "table": {"headers": headers, "rows": rows},
        })


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def parse_docx(path: Path, doc_id: str) -> ParseResult:
    try:
        import docx
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover
        raise ParseError(f"python-docx not installed: {exc}")

    try:
        d = docx.Document(str(path))
    except Exception as exc:
        raise ParseError(f"DOCX open failed: {exc}")

    res = ParseResult()
    # pass 1: collect raw texts for docKey/title detection
    raw_texts = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    res.document_number = detect_doc_number(raw_texts)
    res.title = raw_texts[0][:200] if raw_texts else None

    b = _Builder(doc_id.split("_")[-1][:8])
    b.doc_key = res.document_number

    def blocks():
        for child in d.element.body.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, d)
            elif child.tag == qn("w:tbl"):
                yield Table(child, d)

    for block in blocks():
        if hasattr(block, "text"):  # Paragraph
            text = re.sub(r"\s+", " ", block.text).strip()
            if not text:
                continue
            style = getattr(getattr(block, "style", None), "name", "") or ""
            hm = HEADING_STYLE_RE.match(style)
            if style.lower() in ("title", "название"):
                res.title = text[:200]
                continue
            if hm:
                b.push_heading(int(hm.group(1)), text, None)
            else:
                b.push_paragraph(text, None)
        else:  # Table
            rows = [[re.sub(r"\s+", " ", cell.text).strip() for cell in row.cells]
                    for row in block.rows]
            if not rows:
                continue
            headers, body = rows[0], rows[1:]
            b.push_table(headers, body, None)

    res.fragments = b.fragments
    res.parse_log.append(
        f"docx parsed: {len(res.fragments)} fragments "
        f"({sum(1 for f in res.fragments if f['kind'] == 'clause')} clauses, "
        f"{sum(1 for f in res.fragments if f['kind'] == 'table')} tables)")
    return res


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _pdf_paragraphs(page_text: str) -> list[str]:
    """Group extracted lines into paragraphs; new clause numbers start one."""
    paras: list[str] = []
    cur: list[str] = []
    for line in page_text.splitlines():
        line = line.strip()
        if not line:
            if cur:
                paras.append(" ".join(cur))
                cur = []
            continue
        if CLAUSE_RE.match(line) and cur:
            paras.append(" ".join(cur))
            cur = []
        cur.append(line)
    if cur:
        paras.append(" ".join(cur))
    return paras


def parse_pdf(path: Path, doc_id: str) -> ParseResult:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ParseError(f"pypdf not installed: {exc}")

    try:
        reader = PdfReader(str(path))
        page_texts = [(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)]
    except Exception as exc:
        raise ParseError(f"PDF parsing failed: {exc}")

    res = ParseResult(page_count=len(page_texts))
    total_text = "".join(t for _, t in page_texts).strip()
    if not total_text:
        res.needs_ocr = True
        res.parse_log.append(
            "no extractable text in PDF (likely scanned); OCR would be required "
            "— OCR is out of scope, document left in UPLOADED state")
        return res

    first_texts: list[str] = []
    for _, t in page_texts[:3]:
        first_texts.extend(t.splitlines())
    res.document_number = detect_doc_number(first_texts)
    res.title = next((l.strip() for l in first_texts if l.strip()), None)
    if res.title:
        res.title = res.title[:200]

    b = _Builder(doc_id.split("_")[-1][:8])
    b.doc_key = res.document_number
    heading_re = re.compile(r"^(\d+)\s+[А-ЯЁA-Z][^.]{2,80}$")
    for page_no, text in page_texts:
        for para in _pdf_paragraphs(text):
            para = re.sub(r"\s+", " ", para).strip()
            if not para:
                continue
            hm = heading_re.match(para)
            if hm and len(para) < 90:
                b.push_heading(1, para, page_no)
            else:
                b.push_paragraph(para, page_no)

    res.fragments = b.fragments
    res.parse_log.append(
        f"pdf parsed: {len(page_texts)} pages, {len(res.fragments)} fragments "
        f"({sum(1 for f in res.fragments if f['kind'] == 'clause')} clauses)")
    return res


def parse_document(path: Path, doc_id: str, ext: str) -> ParseResult:
    if ext == ".pdf":
        return parse_pdf(path, doc_id)
    if ext == ".docx":
        return parse_docx(path, doc_id)
    raise ParseError(f"unsupported extension {ext}")
