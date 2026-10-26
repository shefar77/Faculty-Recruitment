"""
STAGE 1 - CV file  ->  plain text.

Deterministic, no LLM, no network. This is the bottom of the pipeline, so it
has one job: produce a faithful, stable text rendering of each CV.

Why "faithful" matters more than "pretty":
  Stage 4 (anti-hallucination) checks that every field the LLM claims to have
  extracted actually appears in THIS text. If we rewrite, reflow or "clean up"
  the text here, honest extractions would start failing that check. So we do
  the bare minimum normalisation and document every bit of it below.

Outputs per input file, into output/:
  <stem>.txt              the extracted text (the evidence source of truth)
  <stem>.extraction.json  provenance: page count, char counts, per-page
                          character offsets, file hash, extractor version

The sidecar JSON exists so later stages can turn a character span in the .txt
back into "page 3 of Candidate_01.pdf" for the audit log.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf  # PyMuPDF (the old `import fitz` name is deprecated)
import docx     # python-docx

EXTRACTOR_VERSION = "stage1-v1"

# A page with fewer than this many characters has no usable text layer.
# Typical text CV page: 1500-4000 chars. A scanned image page: 0-40 chars
# (sometimes a stray header or a watermark).
MIN_CHARS_PER_PAGE = 100

# Whole-file floor, for 1-page files where the per-page average is noisy.
MIN_CHARS_PER_FILE = 200

SUPPORTED_SUFFIXES = {".pdf", ".docx"}

# Privacy (hard rule 6): files should arrive already pseudonymised.
PSEUDONYM_PATTERN = re.compile(r"^Candidate_\d{2,}$", re.IGNORECASE)

# Characters that PDF generators emit constantly and that would silently break
# substring matching in Stage 4. Mapped to a plain space, or deleted entirely.
INVISIBLE_TO_SPACE = (" ", " ", " ")  # nbsp, figure, narrow nbsp
INVISIBLE_TO_DELETE = ("​", "﻿")           # zero-width space, BOM

# Typographic ligatures. A PDF font renders "fi" as the single glyph U+FB01,
# so the extracted text contains a character the LLM will never reproduce -
# it will output a normal "f" + "i", and Stage 4 would then wrongly flag an
# honest extraction as a hallucination. Unlike rejoining hyphenated line
# breaks (a guess), expanding a ligature is lossless and unambiguous, so it
# is safe to do here.
LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}

# C1 control characters (U+0080-U+009F). CVs built with icon fonts (Font
# Awesome and friends) leave these behind where a phone/email/LinkedIn glyph
# used to be. They are invisible, carry no information, and only add noise to
# the evidence text.
C1_CONTROLS = re.compile(r"[\u0080-\u009f]")


@dataclass
class ExtractionResult:
    """Everything we know about one extracted file."""

    source_file: str
    source_sha256: str
    extractor: str
    pages: int
    char_count: int
    chars_per_page: float
    status: str                       # "ok" | "likely_scanned" | "error"
    note: str = ""
    page_offsets: list[dict] = field(default_factory=list)
    text: str = ""                    # not written to the JSON sidecar

    def sidecar(self) -> dict:
        """The JSON we persist - this record minus the text body."""
        d = asdict(self)
        d.pop("text")
        return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _normalise(text: str) -> str:
    """
    The ONLY text surgery we allow. Every transformation here is one that
    Stage 4's "does this string really appear in the source?" check has to
    live with, so each is justified.

    We DO:
      - normalise line endings (CRLF / CR -> LF)
      - map non-breaking and zero-width spaces to a normal space / nothing
      - expand typographic ligatures (U+FB01 -> "fi"), which is lossless
      - drop C1 control characters left behind by icon fonts
      - strip trailing whitespace on each line
      - collapse 3+ blank lines into 2 (purely so the .txt stays readable)

    We deliberately DO NOT:
      - rejoin hyphenated line breaks ("Machine Lear-\\nning"): un-hyphenating
        is a guess, and a wrong guess corrupts the evidence text.
      - collapse runs of spaces inside a line: those runs are how PDF tables
        and two-column layouts survive, and publication lists are often tables.
      - lowercase, strip accents, or reorder anything.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for ch in INVISIBLE_TO_SPACE:
        text = text.replace(ch, " ")
    for ch in INVISIBLE_TO_DELETE:
        text = text.replace(ch, "")
    for ligature, expansion in LIGATURES.items():
        text = text.replace(ligature, expansion)
    text = C1_CONTROLS.sub("", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    # A page of pure whitespace must come back as exactly "" (0 chars), not as
    # a lone newline - classify() keys "no text layer at all" off char_count 0.
    return text + "\n" if text else ""


def extract_pdf(path: Path, sort_blocks: bool = True) -> tuple[str, list[dict]]:
    """
    PDF -> (text, page_offsets).

    sort_blocks=True asks PyMuPDF to emit text blocks in geometric reading
    order (top-to-bottom, then left-to-right) instead of raw PDF
    content-stream order, which is frequently scrambled. That is the right
    choice for the single-column CVs we expect; if a CV is genuinely
    two-column, run with --no-sort and compare the two .txt files.

    page_offsets records where each page's text lands in the combined string,
    so a character span can later be reported as "page N" in the audit log.
    The combined string is assembled piece by piece while tracking the cursor,
    rather than normalised again at the end, so that text[start:end] is
    guaranteed to be exactly that page's text - offsets cannot drift.
    """
    pages: list[tuple[str, bool]] = []
    with pymupdf.open(path) as doc:
        for page in doc:
            page_text = _normalise(page.get_text("text", sort=sort_blocks))
            pages.append((page_text, len(page.get_images(full=True)) > 0))

    chunks: list[str] = []
    offsets: list[dict] = []
    cursor = 0

    for page_index, (page_text, has_images) in enumerate(pages, start=1):
        if page_text:
            if chunks:                     # blank line between pages
                chunks.append("\n")
                cursor += 1
            chunks.append(page_text)
            offsets.append(
                {
                    "page": page_index,
                    "start": cursor,
                    "end": cursor + len(page_text),
                    "chars": len(page_text),
                    "has_images": has_images,
                }
            )
            cursor += len(page_text)
        else:
            # An empty page still gets an entry, as a zero-width span, so that
            # page numbers in the sidecar always line up with the real PDF.
            offsets.append(
                {
                    "page": page_index,
                    "start": cursor,
                    "end": cursor,
                    "chars": 0,
                    "has_images": has_images,
                }
            )

    text = "".join(chunks)

    # The sidecar's whole promise is that text[start:end] is that page's text.
    # Verify it instead of trusting the arithmetic.
    for (page_text, _), offset in zip(pages, offsets):
        assert text[offset["start"]:offset["end"]] == page_text, (
            f"page offset drift on page {offset['page']} of {path.name}"
        )

    return text, offsets


def extract_docx(path: Path) -> tuple[str, list[dict]]:
    """
    DOCX -> (text, page_offsets).

    A .docx has no fixed pages (pagination happens at render time), so we
    report one logical "page". We pull paragraphs AND table cells, because
    publication lists in Word CVs are very often tables - skipping them would
    gut the single most important section for this project.
    """
    document = docx.Document(str(path))
    lines: list[str] = [p.text for p in document.paragraphs]

    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            # Two spaces keep a visible column gap without inventing punctuation.
            lines.append("  ".join(c for c in cells if c))

    text = _normalise("\n".join(lines))
    offsets = [
        {
            "page": 1,
            "start": 0,
            "end": len(text),
            "chars": len(text),
            "has_images": False,
        }
    ]
    return text, offsets


def classify(pages: int, char_count: int, offsets: list[dict]) -> tuple[str, str]:
    """Decide whether this file actually gave us usable text."""
    per_page = char_count / pages if pages else 0.0

    if char_count == 0:
        return "likely_scanned", "no text layer at all - likely scanned; OCR deferred"

    if char_count < MIN_CHARS_PER_FILE or per_page < MIN_CHARS_PER_PAGE:
        return (
            "likely_scanned",
            f"only {char_count} chars ({per_page:.0f}/page) - likely scanned or "
            f"image-only; OCR deferred",
        )

    thin = [o["page"] for o in offsets if o["chars"] < MIN_CHARS_PER_PAGE]
    if thin:
        return (
            "ok",
            f"usable overall, but page(s) {thin} have almost no text "
            f"(possibly scanned inserts)",
        )

    return "ok", ""


def extract_file(path: Path, sort_blocks: bool = True) -> ExtractionResult:
    """Extract one file. Never raises - a bad file comes back as status='error'."""
    suffix = path.suffix.lower()
    base = {
        "source_file": path.name,
        "source_sha256": _sha256(path),
        "extractor": EXTRACTOR_VERSION,
    }

    try:
        if suffix == ".pdf":
            text, offsets = extract_pdf(path, sort_blocks=sort_blocks)
        elif suffix == ".docx":
            text, offsets = extract_docx(path)
        else:
            return ExtractionResult(
                **base,
                pages=0,
                char_count=0,
                chars_per_page=0.0,
                status="error",
                note=f"unsupported file type '{suffix}'",
            )
    except Exception as exc:  # corrupt, encrypted, or otherwise unreadable
        return ExtractionResult(
            **base,
            pages=0,
            char_count=0,
            chars_per_page=0.0,
            status="error",
            note=f"{type(exc).__name__}: {exc}",
        )

    pages = len(offsets)
    char_count = len(text)
    status, note = classify(pages, char_count, offsets)

    return ExtractionResult(
        **base,
        pages=pages,
        char_count=char_count,
        chars_per_page=round(char_count / pages, 1) if pages else 0.0,
        status=status,
        note=note,
        page_offsets=offsets,
        text=text,
    )


def write_outputs(result: ExtractionResult, out_dir: Path, stem: str) -> Path:
    """Write <stem>.txt and <stem>.extraction.json. Returns the .txt path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / f"{stem}.txt"
    txt_path.write_text(result.text, encoding="utf-8")
    (out_dir / f"{stem}.extraction.json").write_text(
        json.dumps(result.sidecar(), indent=2), encoding="utf-8"
    )
    return txt_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _collect_inputs(args: list[str], cv_dir: Path) -> list[Path]:
    """Explicit paths if given, else every supported file in data/cvs/."""
    if args:
        return [Path(a) for a in args]
    if not cv_dir.is_dir():
        return []
    return sorted(
        p for p in cv_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def main(argv: list[str]) -> int:
    sort_blocks = "--no-sort" not in argv
    path_args = [a for a in argv if not a.startswith("--")]

    project_root = Path(__file__).resolve().parent.parent
    cv_dir = project_root / "data" / "cvs"
    out_dir = project_root / "output"

    files = _collect_inputs(path_args, cv_dir)

    if not files:
        print(f"No .pdf/.docx files found in {cv_dir}")
        print("Put pseudonymised CVs there as Candidate_01.pdf, Candidate_02.pdf, ...")
        return 1

    print(
        f"Stage 1: text extraction  ({len(files)} file(s), "
        f"block sorting {'on' if sort_blocks else 'off'})\n"
    )
    header = f"{'file':<28} {'pages':>5} {'chars':>8} {'chars/pg':>9}  status"
    print(header)
    print("-" * len(header))

    results: list[ExtractionResult] = []
    unpseudonymised: list[str] = []

    for path in files:
        if not path.is_file():
            print(f"{path.name:<28} {'-':>5} {'-':>8} {'-':>9}  error: file not found")
            continue

        result = extract_file(path, sort_blocks=sort_blocks)
        results.append(result)

        if result.status != "error":
            write_outputs(result, out_dir, path.stem)

        if not PSEUDONYM_PATTERN.match(path.stem):
            unpseudonymised.append(path.name)

        print(
            f"{path.name:<28} {result.pages:>5} {result.char_count:>8} "
            f"{result.chars_per_page:>9}  {result.status}"
        )
        if result.note:
            print(f"{'':<28} {'':>5} {'':>8} {'':>9}  ^ {result.note}")

    ok = sum(1 for r in results if r.status == "ok")
    scanned = sum(1 for r in results if r.status == "likely_scanned")
    errors = sum(1 for r in results if r.status == "error")

    print(f"\n{ok} ok | {scanned} likely scanned (OCR deferred) | {errors} error")
    print(f"Text + provenance written to {out_dir}")

    if unpseudonymised:
        print(
            "\nPrivacy note: these filenames are not in Candidate_NN form, so they "
            "may carry real identities:"
        )
        for name in unpseudonymised:
            print(f"  - {name}")

    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
