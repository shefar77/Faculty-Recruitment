"""
STAGE 7c - the end-to-end runner.

    python run.py data/cvs/Candidate_01.pdf
    python run.py                      # every CV in data/cvs/
    python run.py --no-llm             # skip Stage 7 prose (templates only)
    python run.py --as-of=2026-10-15   # pin "Present" for reproducibility

Chains stages 1 -> 7 and writes ONE audit file per candidate containing
every intermediate output, then stores the result in SQLite.

WHY THE AUDIT FILE HOLDS EVERYTHING
  A score is only defensible if someone can retrace it. The file therefore
  carries the extracted text's hash, the raw model output, what validation
  rejected and why, every OpenAlex query URL, the computed criteria, and
  the model/prompt fingerprints behind each explanation. A reviewer who
  disagrees with a number can follow it back to the characters in the PDF
  that produced it.

REPRODUCIBILITY
  Anything that could make the same CV score differently on a different
  day is pinned and recorded: the rubric version and its full weight
  table, the as-of date that resolves "Present", the model name, and a
  hash of every prompt used.
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import extract_text    # noqa: E402
import extract_fields  # noqa: E402
import validate        # noqa: E402
import verify_pubs     # noqa: E402
import scoring         # noqa: E402
import explain         # noqa: E402
import store           # noqa: E402

PIPELINE_VERSION = "stage7-v1"
OUTPUT_DIR = ROOT / "output"


def run_candidate(cv_path: Path, *, as_of: date, use_llm: bool = True,
                  use_cache: bool = True,
                  reuse_extraction: bool = False,
                  department: str = scoring.degrees.DEFAULT_DEPARTMENT) -> dict:
    """
    Run one CV through every stage and return the complete audit record.

    `department` is the advertised post's discipline. It reaches only the
    branch-relevance factor on the two degree categories, and it is
    recorded in the audit so a score can never be compared against one
    computed for a different post.
    """
    stem = cv_path.stem
    started = datetime.now(timezone.utc)

    # --- Stage 1: text ----------------------------------------------------
    extraction = extract_text.extract_file(cv_path)
    if extraction.status == "error":
        raise RuntimeError(f"text extraction failed: {extraction.note}")

    # "likely_scanned" must abort too, not just "error". A PDF with no text
    # layer extracts to nothing, the model finds nothing in nothing, and
    # the candidate scores 0.0/100 with eligibility 'not_met' - recorded as
    # unqualified when the truth is that we never read their CV.
    #
    # This is a real case, not a corner one: a scanned CV is an ordinary
    # thing to receive. The right answer is to refuse and say why, so
    # someone can supply a text PDF or run OCR, which this project does
    # not do (see Stage 1).
    if extraction.status == "likely_scanned":
        raise RuntimeError(
            f"no usable text layer ({extraction.char_count} characters from "
            f"{extraction.pages} page(s)): {extraction.note}. The CV cannot "
            f"be scored - supply a text-based PDF, or OCR it first. Scoring "
            f"it anyway would record the candidate as unqualified when in "
            f"fact their CV was never read.")
    extract_text.write_outputs(extraction, OUTPUT_DIR, stem)
    cv_text = extraction.text

    # --- Stage 3: text -> JSON (three separate calls, see extract_fields) --
    # Extraction is by far the most expensive stage - minutes per CV on
    # CPU - and it is deterministic, so re-running it to change a weight
    # or re-word an explanation is pure waste. --reuse-extraction replays
    # the stored output instead. It refuses to run if the stored record
    # came from a DIFFERENT file, so a stale record can never be scored
    # against the wrong candidate.
    extracted_path = OUTPUT_DIR / f"{stem}.extracted.json"
    if reuse_extraction and extracted_path.is_file():
        extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
        stored_hash = extracted.get("source_sha256")
        if stored_hash and stored_hash != extraction.source_sha256:
            raise RuntimeError(
                f"stored extraction is for a different file "
                f"(sha {stored_hash[:12]} vs {extraction.source_sha256[:12]}); "
                f"re-run without --reuse-extraction")
        extracted["reused_from_disk"] = True
    else:
        extracted = extract_fields.extract_candidate(cv_text, stem)
    extracted["source_sha256"] = extraction.source_sha256

    # ABORT if any extraction call failed. An unreachable model yields an
    # empty-but-valid profile that scores 0.0/100 and gates 'not_met',
    # which is indistinguishable from a genuinely unqualified candidate.
    # Publishing that would be worse than publishing nothing, so the run
    # stops here and the record is written under a different name so it
    # can never be mistaken for a successful extraction.
    failures = extract_fields.extraction_failures(extracted)
    if failures:
        (OUTPUT_DIR / f"{stem}.extraction-FAILED.json").write_text(
            json.dumps(extracted, indent=2, ensure_ascii=False),
            encoding="utf-8")
        raise RuntimeError(
            "extraction failed, refusing to score: " + "; ".join(failures))

    extracted_path.write_text(
        json.dumps(extracted, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Stage 4: validation ---------------------------------------------
    validated = validate.validate_record(extracted, cv_text)
    (OUTPUT_DIR / f"{stem}.validated.json").write_text(
        json.dumps(validated, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Stage 5: publication verification -------------------------------
    verified = verify_pubs.verify_candidate(validated, use_cache=use_cache)
    (OUTPUT_DIR / f"{stem}.verified.json").write_text(
        json.dumps(verified, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Stage 6: deterministic scoring ----------------------------------
    scored = scoring.score_candidate(validated, verified, as_of=as_of,
                                     department=department)
    (OUTPUT_DIR / f"{stem}.scored.json").write_text(
        json.dumps(scored, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Stage 7: explanations -------------------------------------------
    explained = explain.explain_candidate(scored, use_llm=use_llm)
    (OUTPUT_DIR / f"{stem}.explained.json").write_text(
        json.dumps(explained, indent=2, ensure_ascii=False), encoding="utf-8")

    finished = datetime.now(timezone.utc)

    audit = {
        "candidate_id": stem,
        "pipeline_version": PIPELINE_VERSION,
        "run_started_at": started.isoformat(timespec="seconds"),
        "run_finished_at": finished.isoformat(timespec="seconds"),
        "run_seconds": round((finished - started).total_seconds(), 1),
        "as_of_date": as_of.isoformat(),
        "source": {
            "file": cv_path.name,
            "sha256": extraction.source_sha256,
            "pages": extraction.pages,
            "chars": extraction.char_count,
            "extractor": extraction.extractor,
        },
        "models": {
            "extraction_model": extract_fields.MODEL,
            "temperature": extract_fields.TEMPERATURE,
            "seed": extract_fields.SEED,
            "num_ctx": extract_fields.NUM_CTX,
            "llm_used_for_explanations": use_llm,
            "extraction_reused_from_disk": bool(extracted.get("reused_from_disk")),
        },
        # Full intermediate outputs, so the audit file stands alone.
        "extraction": extracted,
        "validation": validated,
        "verification": verified,
        "scoring": scored,
        "explanations": explained,
        "human_decision_required": True,
        "disclaimer": ("Scores come from a DEMO/RESEARCH rubric, not LNMIIT "
                       "hiring criteria. Publication verification is assisted "
                       "matching, never proof of authorship. The final "
                       "decision rests with the faculty reviewer."),
    }

    (OUTPUT_DIR / f"{stem}.audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    return audit


def main(argv: list[str]) -> int:
    use_llm = "--no-llm" not in argv
    use_cache = "--no-cache" not in argv
    reuse = "--reuse-extraction" in argv
    as_of = date.today()
    for arg in argv:
        if arg.startswith("--as-of="):
            as_of = date.fromisoformat(arg.split("=", 1)[1])

    paths = [Path(a) for a in argv if not a.startswith("--")]
    if not paths:
        cv_dir = ROOT / "data" / "cvs"
        paths = sorted(p for p in cv_dir.iterdir()
                       if p.suffix.lower() in extract_text.SUPPORTED_SUFFIXES)
    if not paths:
        print("No CVs found. Put PDFs in data/cvs/.")
        return 1

    print(f"Pipeline {PIPELINE_VERSION}  |  {len(paths)} CV(s)  |  "
          f"as-of {as_of}  |  llm {'on' if use_llm else 'off'}"
          f"{'  |  reusing stored extraction' if reuse else ''}")
    print("DEMO/RESEARCH rubric - NOT LNMIIT hiring criteria\n")

    connection = store.connect()
    failures = 0

    for cv_path in paths:
        print(f"{cv_path.name} ... ", end="", flush=True)
        try:
            audit = run_candidate(cv_path, as_of=as_of, use_llm=use_llm,
                                  use_cache=use_cache, reuse_extraction=reuse)
        except Exception as exc:
            failures += 1
            print(f"FAILED: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=2)
            continue

        store.save_audit(connection, audit)
        scored = audit["scoring"]
        print(f"{scored['total_score']:>6.2f}/100  "
              f"{scored['eligibility']['outcome']:<14} "
              f"({audit['run_seconds']:.0f}s)")

    print("\n--- ranked (eligible first, gated below, never interleaved) ---")
    print(f"  {'candidate':<14}{'computed':>9}{'effective':>11}  "
          f"{'eligibility':<15}name")
    for row in store.ranked_candidates(connection):
        # Both figures are shown always, so an overridden score can never be
        # mistaken for one the pipeline produced.
        marker = "  <- overridden" if row["is_overridden"] else ""
        eligibility = row["effective_eligibility"]
        if eligibility != row["computed_eligibility"]:
            eligibility = f"{eligibility} (was {row['computed_eligibility']})"
        print(f"  {row['candidate_id']:<14}{row['computed_total']:>9.2f}"
              f"{row['effective_total']:>11.2f}  {eligibility:<15}"
              f"{(row['candidate_name'] or '')[:24]}{marker}")

    print(f"\nAudit files: {OUTPUT_DIR}/*.audit.json")
    print(f"Database   : {store.DB_PATH}")
    connection.close()
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
