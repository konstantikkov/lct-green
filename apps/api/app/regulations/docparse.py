"""Document parsing: PDF (pypdf), DOCX (python-docx) and RTF (striprtf)
→ hierarchy fragments.

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
TABLE_CAPTION_RE = re.compile(r"^Таблица\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
# OCR/PDF/RTF line that opens a numbered top-level section ("9 Насаждения…")
SECTION_HEADING_RE = re.compile(r"^(\d+)\s+[А-ЯЁA-Z][^.]{2,80}$")

# Number sign as it actually appears in Russian normative texts. Official
# publications and КонсультантПлюс/docs.cntd exports write Latin "N" far more
# often than "№"; Cyrillic "Н" also turns up in OCR output.
_NUMSIGN = r"(?:№|N|Н)"
DOCNUM_RE = re.compile(
    r"(СП\s*[\d.]+\d"
    r"|ГОСТ(?:\s*Р)?\s*[\d.\-]+\d"
    r"|СНиП\s*[\d.\-]+\d"
    r"|(?:ТСН|МГСН)\s*[\d.\-]+\d"
    # ППМ № 743-ПП / ПОСТАНОВЛЕНИЕ … N 743-ПП / just "623-ПП"
    rf"|ППМ\s*{_NUMSIGN}?\s*[\d\-]+(?:-ПП)?"
    rf"|{_NUMSIGN}\s*\d+\s*-\s*ПП"
    r"|\b\d{2,4}-ПП\b"
    # Закон города Москвы № 18 — no -ПП suffix, so anchor on the word "закон"
    rf"|ЗАКОН[^\n]{{0,60}}?{_NUMSIGN}\s*\d+"
    r")", re.IGNORECASE)
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


def normalize_doc_number(raw: str) -> str:
    """Canonical form of a detected number, so the docKey is stable and readable.

    The same act is printed as "N 743-ПП", "№ 743-ПП" or "743-ПП" depending on
    the publisher; all three must collapse to one key, because the key prefixes
    every fragment id.

        "N 743-ПП"                          → "743-ПП"
        "Закон Москвы от 30.04.2014 г. № 18" → "Закон № 18"
    """
    s = re.sub(r"\s+", " ", raw).strip()
    m = re.match(rf"^ЗАКОН.*?{_NUMSIGN}\s*(\d+)$", s, re.IGNORECASE)
    if m:
        return f"Закон № {m.group(1)}"
    # drop a leading number sign ("N", "№", "Н") and tighten "743 - ПП"
    s = re.sub(rf"^(?:ППМ\s*)?{_NUMSIGN}\s*", "", s)
    s = re.sub(r"\s*-\s*ПП$", "-ПП", s, flags=re.IGNORECASE)
    return s.strip()


def detect_doc_number(texts: list[str]) -> str | None:
    """Scan the first pages/paragraphs for a normative document number."""
    for t in texts[:60]:
        m = DOCNUM_RE.search(t or "")
        if m:
            return normalize_doc_number(m.group(1))
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
    """Shared fragment builder maintaining heading stack + global order.

    Besides the heading stack it tracks two OCR-robust contexts (audit item 3):

    * clause context — a paragraph starting with a clause number X.Y updates
      the section context to "X > X.Y" even when the section heading itself
      was missed by the heading regex (common in OCR'd PDFs), so subsequent
      caption/row fragments are not attributed to a stale earlier section;
    * "Таблица N[.M]" context — after a table caption, subsequent fragments
      (same page or the next one, until the next heading/clause) get
      " > Таблица N[.M]" appended to their hierarchyPath.
    """

    def __init__(self, doc_key_fallback: str):
        self.doc_key_fallback = doc_key_fallback
        self.doc_key: str | None = None
        self.headings: list[tuple[int, str]] = []  # (level, title)
        self.fragments: list[dict] = []
        self.order = 0
        self.clause_ctx: str | None = None   # e.g. "9 > 9.6"
        self.table_ctx: str | None = None    # e.g. "Таблица 9.1"
        self.table_page: int | None = None

    def _key(self) -> str:
        return slugify(self.doc_key, 32) if self.doc_key else self.doc_key_fallback

    def _slug_trail(self) -> str:
        parts = [slugify(t) for _, t in self.headings[:2]]  # h1 (+ h2) slugs
        return ":".join(parts) if parts else "root"

    def _heading_path(self) -> str:
        return " > ".join(t for _, t in self.headings)

    def _base_path(self) -> str:
        return self.clause_ctx or self._heading_path()

    def _table_active(self, page: int | None) -> bool:
        if not self.table_ctx:
            return False
        if page is None or self.table_page is None:
            return True
        return page - self.table_page <= 1  # same page or the next one

    def push_heading(self, level: int, text: str, page: int | None) -> None:
        self.headings = [h for h in self.headings if h[0] < level]
        self.headings.append((level, text))
        self.clause_ctx = None
        self.table_ctx = None
        self.order += 1
        self.fragments.append({
            "id": f"{self._key()}:{self._slug_trail()}:p{self.order}",
            "page": page, "hierarchyPath": self._heading_path(),
            "kind": "heading", "text": text, "order": self.order,
        })

    def push_paragraph(self, text: str, page: int | None) -> None:
        self.order += 1
        kind = "clause"
        cm = CLAUSE_RE.match(text)
        tm = TABLE_CAPTION_RE.match(text)
        if cm:
            num = cm.group(1)
            parts = num.split(".")
            path = " > ".join(".".join(parts[:i + 1]) for i in range(len(parts)))
            # clause number at paragraph start = section context "X > X.Y":
            # trust it even when the heading regex missed the section heading
            self.clause_ctx = path
            self.table_ctx = None  # a new clause ends the table region
        elif tm:
            label = f"Таблица {tm.group(1)}"
            self.table_ctx, self.table_page = label, page
            base = self._base_path()
            path = f"{base} > {label}" if base else label
        else:
            path = self._base_path()
            if self._table_active(page):
                path = f"{path} > {self.table_ctx}" if path else self.table_ctx
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
        path = self._base_path()
        if self._table_active(page):
            path = f"{path} > {self.table_ctx}" if path else self.table_ctx
        self.fragments.append({
            "id": f"{self._key()}:{self._slug_trail()}:t{self.order}",
            "page": page, "hierarchyPath": path or "Таблица",
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
    """Group extracted lines into paragraphs; new clause numbers start one.
    Table captions ("Таблица N.M") always form their own paragraph so the
    builder can open a table context exactly at the caption."""
    paras: list[str] = []
    cur: list[str] = []
    for line in page_text.splitlines():
        line = line.strip()
        if not line:
            if cur:
                paras.append(" ".join(cur))
                cur = []
            continue
        if TABLE_CAPTION_RE.match(line):
            if cur:
                paras.append(" ".join(cur))
                cur = []
            paras.append(line)
            continue
        if CLAUSE_RE.match(line) and cur:
            paras.append(" ".join(cur))
            cur = []
        cur.append(line)
    if cur:
        paras.append(" ".join(cur))
    return paras


# ---------------------------------------------------------------------------
# OCR fallback for scanned PDFs (tesseract + poppler's pdftoppm)
# ---------------------------------------------------------------------------

def ocr_available() -> bool:
    import shutil as _sh
    return bool(_sh.which("tesseract") and _sh.which("pdftoppm"))


def ocr_pdf_pages(path: Path, page_count: int, dpi: int = 200,
                  langs: str = "rus+eng", progress=None) -> list[tuple[int, str]]:
    """Renders each page with pdftoppm and OCRs it with tesseract.
    Safe subprocess invocation (argv lists, per-step timeouts). Returns
    (page_no, text) pairs, same shape as native extraction."""
    import subprocess
    import tempfile

    out: list[tuple[int, str]] = []
    with tempfile.TemporaryDirectory(prefix="ocr_") as tmp:
        tmpdir = Path(tmp)
        for page in range(1, page_count + 1):
            base = tmpdir / f"pg_{page}"
            r = subprocess.run(
                ["pdftoppm", "-r", str(dpi), "-gray", "-png",
                 "-f", str(page), "-l", str(page), str(path), str(base)],
                capture_output=True, timeout=120, shell=False)
            imgs = sorted(tmpdir.glob(f"pg_{page}*.png"))
            if r.returncode != 0 or not imgs:
                out.append((page, ""))
                continue
            t = subprocess.run(
                ["tesseract", str(imgs[0]), "stdout", "-l", langs, "--psm", "4"],
                capture_output=True, timeout=180, shell=False)
            out.append((page, t.stdout.decode("utf-8", errors="replace")
                        if t.returncode == 0 else ""))
            for img in imgs:
                img.unlink(missing_ok=True)
            if progress:
                progress(page, page_count)
    return out


def build_pdf_result(page_texts: list[tuple[int, str]], doc_id: str,
                     via_ocr: bool = False) -> ParseResult:
    """Hierarchy/fragment construction shared by native and OCR extraction."""
    res = ParseResult(page_count=len(page_texts))
    first_texts: list[str] = []
    for _, t in page_texts[:3]:
        first_texts.extend(t.splitlines())
    res.document_number = detect_doc_number(first_texts)
    res.title = next((l.strip() for l in first_texts if l.strip()), None)
    if res.title:
        res.title = res.title[:200]

    b = _Builder(doc_id.split("_")[-1][:8])
    b.doc_key = res.document_number
    for page_no, text in page_texts:
        for para in _pdf_paragraphs(text):
            para = re.sub(r"\s+", " ", para).strip()
            if not para:
                continue
            hm = SECTION_HEADING_RE.match(para)
            if hm and len(para) < 90:
                b.push_heading(1, para, page_no)
            else:
                b.push_paragraph(para, page_no)

    res.fragments = b.fragments
    res.parse_log.append(
        f"pdf parsed{' via OCR (tesseract)' if via_ocr else ''}: "
        f"{len(page_texts)} pages, {len(res.fragments)} fragments "
        f"({sum(1 for f in res.fragments if f['kind'] == 'clause')} clauses)")
    return res


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

    total_text = "".join(t for _, t in page_texts).strip()
    if not total_text:
        res = ParseResult(page_count=len(page_texts))
        res.needs_ocr = True
        if ocr_available():
            res.parse_log.append(
                "no extractable text in PDF (scanned document) — OCR fallback "
                "available, queueing tesseract recognition")
        else:
            res.parse_log.append(
                "no extractable text in PDF (scanned document); install "
                "tesseract-ocr + tesseract-ocr-rus + poppler-utils to enable OCR")
        return res
    return build_pdf_result(page_texts, doc_id)


def parse_pdf_ocr(path: Path, doc_id: str, page_count: int,
                  progress=None) -> ParseResult:
    """Full OCR parse of a scanned PDF (minutes for large documents)."""
    page_texts = ocr_pdf_pages(path, page_count, progress=progress)
    recognized = sum(1 for _, t in page_texts if t.strip())
    if recognized == 0:
        raise ParseError("OCR produced no text on any page")
    res = build_pdf_result(page_texts, doc_id, via_ocr=True)
    res.parse_log.insert(0, f"OCR recognized text on {recognized}/{len(page_texts)} pages")
    return res


# ---------------------------------------------------------------------------
# RTF (striprtf) — e.g. the official СП 42.13330 source distribution
# ---------------------------------------------------------------------------

def parse_rtf(path: Path, doc_id: str) -> ParseResult:
    """RTF → text via striprtf, then the same _Builder flow as PDFs:
    no page numbers (page=None), clause numbers X.Y[.Z] at line start drive
    the hierarchy, and "Таблица N.M" captions open a table context."""
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as exc:  # pragma: no cover
        raise ParseError(f"striprtf not installed: {exc}")

    try:
        raw = path.read_bytes()
        try:
            src = raw.decode("utf-8")
        except UnicodeDecodeError:
            src = raw.decode("cp1251", errors="replace")
        text = rtf_to_text(src, errors="ignore")
    except Exception as exc:
        raise ParseError(f"RTF parsing failed: {exc}")
    if not text.strip():
        raise ParseError("RTF contains no extractable text")

    res = ParseResult(page_count=None)  # RTF has no page geometry
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    res.document_number = detect_doc_number(lines)
    res.title = lines[0][:200] if lines else None

    b = _Builder(doc_id.split("_")[-1][:8])
    b.doc_key = res.document_number
    for line in lines:  # striprtf emits one line per \par — 1 line = 1 para
        para = re.sub(r"\s+", " ", line).strip()
        if not para:
            continue
        hm = SECTION_HEADING_RE.match(para)
        if hm and len(para) < 90:
            b.push_heading(1, para, None)
        else:
            b.push_paragraph(para, None)

    res.fragments = b.fragments
    res.parse_log.append(
        f"rtf parsed: {len(res.fragments)} fragments "
        f"({sum(1 for f in res.fragments if f['kind'] == 'clause')} clauses)")
    return res


def parse_document(path: Path, doc_id: str, ext: str) -> ParseResult:
    if ext == ".pdf":
        return parse_pdf(path, doc_id)
    if ext == ".docx":
        return parse_docx(path, doc_id)
    if ext == ".rtf":
        return parse_rtf(path, doc_id)
    raise ParseError(f"unsupported extension {ext}")
