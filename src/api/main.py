"""
STAGE 8 - FastAPI over the finished pipeline.

    uvicorn src.api.main:app --reload --port 8000

THIS LAYER CONTAINS NO PIPELINE LOGIC.
  Every endpoint is a thin translation between HTTP and a function that
  already existed and was already tested:

    POST   /candidates        -> run.run_candidate() + store.save_audit()
    GET    /candidates        -> store.ranked_candidates()
    GET    /candidates/{id}   -> store.candidate_detail()
    PATCH  /candidates/{id}/score -> store.record_override()

  No scoring, validation, verification or override arithmetic is
  reimplemented here. Where an endpoint needed something the pipeline did
  not expose - the per-candidate evidence bundle - that was added to
  src/store.py, not to this file, so the behaviour stays covered by the
  existing test suite and stays usable without HTTP.

  The rules proven in Stage 7 therefore hold automatically, because the
  same code enforces them: computed_total is immutable, effective_total
  is computed from deltas, a blank reason raises, and a foreign key stops
  an override being filed against a candidate who does not exist.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))          # for run.py
sys.path.insert(0, str(ROOT / "src"))  # for the pipeline modules

import run      # noqa: E402  the Stage 7 orchestrator, imported unchanged
import store    # noqa: E402
import explain  # noqa: E402
import schema   # noqa: E402
import scoring  # noqa: E402
import roles    # noqa: E402
import degrees       # noqa: E402
import institutions  # noqa: E402

CV_DIR = ROOT / "data" / "cvs"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
ALLOWED_SUFFIXES = {".pdf", ".docx"}

app = FastAPI(
    title="Faculty Recruitment Screening API",
    version="stage8-v1",
    description=(
        "HTTP access to a deterministic CV screening pipeline. Scores come "
        "from a DEMO/RESEARCH rubric, not LNMIIT hiring criteria. "
        "Publication checks are assisted matching, never proof of "
        "authorship. Every decision remains the reviewer's."
    ),
)

# The Stage 9 frontend runs on a different port under Vite.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)


def get_db():
    connection = store.connect()
    try:
        yield connection
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class OverrideRequest(BaseModel):
    """A human overriding one computed value."""

    field: str = Field(..., description=(
        "A criterion name (e.g. 'teaching_experience') or 'eligibility'."))
    new_value: float | str = Field(..., description=(
        "New points for a criterion, or one of met/not_met/indeterminate "
        "for eligibility."))
    reason: str = Field(..., min_length=1, description=(
        "Why. Mandatory - an unexplained override is indistinguishable "
        "from a mistake when read months later."))
    overridden_by: str = Field("faculty", description="Who made the call.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "pipeline": run.PIPELINE_VERSION}


@app.post("/candidates", status_code=201)
async def upload_candidate(
    file: UploadFile = File(...),
    use_llm: bool = Query(False, description=(
        "Generate prose explanations during the upload. Defaults to OFF: "
        "prose is the one output that cannot change a score, and generating "
        "it here costs about a quarter of the run for text the reviewer may "
        "never open. Every criterion still gets a deterministic template "
        "sentence. Call POST /candidates/{id}/explain for prose on demand.")),
    reuse_extraction: bool = Query(False, description=(
        "Replay a stored extraction for this filename instead of calling "
        "the model again. Refuses if the stored record is for a different "
        "file.")),
    as_of: date | None = Query(None, description=(
        "Date against which ongoing roles are measured. Pin it to "
        "reproduce a past run exactly.")),
    department: str = Query(degrees.DEFAULT_DEPARTMENT, description=(
        "The advertised post's discipline. It affects branch relevance on "
        "the bachelor's and master's categories and nothing else. Recorded "
        "in the audit, so scores computed for different departments cannot "
        "be compared by accident.")),
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Upload a CV, run the whole pipeline, return the complete audit record.

    SLOW BY NATURE: a full run is minutes per CV on CPU, because a local
    7B model reads the CV three times. Measured on the development machine,
    99.9% of the runtime is Ollama generating tokens at ~8/sec; text
    extraction, validation, OpenAlex lookups, scoring and the database
    together account for 0.1 seconds. That is the cost of the zero-API
    constraint, not a defect in this code.

    Prose explanations are OFF by default here for the reason given on the
    parameter. Pass use_llm=true to generate them inline instead.
    """
    name = Path(file.filename or "").name
    if not name or not SAFE_NAME.match(name):
        raise HTTPException(400, "filename must be plain ASCII without path "
                                 "separators")
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"unsupported file type; expected one of "
                                 f"{sorted(ALLOWED_SUFFIXES)}")

    CV_DIR.mkdir(parents=True, exist_ok=True)
    destination = CV_DIR / name
    destination.write_bytes(await file.read())

    # The pipeline is minutes of blocking CPU work. Running it directly in
    # this coroutine would freeze the whole event loop - every other
    # request, including a reviewer simply loading the ranked list, would
    # hang until it finished. run_in_threadpool moves it off the loop.
    try:
        audit = await run_in_threadpool(
            run.run_candidate, destination,
            as_of=as_of or date.today(),
            use_llm=use_llm, reuse_extraction=reuse_extraction,
            department=department)
    except Exception as exc:
        raise HTTPException(422, f"pipeline failed: {type(exc).__name__}: {exc}")

    store.save_audit(connection, audit)
    return audit


@app.get("/candidates")
def list_candidates(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    """
    The ranked list, in two groups that are never interleaved.

    Eligible candidates rank by effective_total. Gated ones are returned
    separately rather than mixed in or dropped, because "scored 19.35 but
    not yet eligible" and "scored 19.35 and appointable" call for
    different decisions (DESIGN_DECISIONS.md section 3).
    """
    rows = store.ranked_candidates(connection)
    eligible = [r for r in rows if r["effective_eligibility"] == "met"]
    gated = [r for r in rows if r["effective_eligibility"] != "met"]

    unreliable = [r for r in rows if not r.get("scoring_reliable", True)]

    return {
        "eligible": eligible,
        "gated": gated,
        # Loud, at the top of the payload: if a lookup service was down
        # during scoring, these rankings are not trustworthy and the UI
        # must say so rather than render a normal-looking table.
        "scoring_warnings": [
            {"candidate_id": r["candidate_id"],
             "reasons": r.get("unreliable_reasons", [])}
            for r in unreliable],
        "all_scores_reliable": not unreliable,
        "counts": {"eligible": len(eligible), "gated": len(gated),
                   "total": len(rows)},
        "ordering": ("eligible first by effective_total, then gated by "
                     "effective_total; the two groups are never interleaved"),
        "note": ("Both computed_total and effective_total are returned for "
                 "every candidate. effective_total reflects human overrides "
                 "and is what the ranking uses; computed_total is the "
                 "pipeline's own figure and is never altered."),
        "rubric_disclaimer": ("DEMO/RESEARCH rubric - not LNMIIT hiring "
                              "criteria."),
    }


@app.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: str,
                  connection: sqlite3.Connection = Depends(get_db)) -> dict:
    """
    Full evidence for one candidate: per-criterion computed vs effective
    values, override history, publication verdicts with their OpenAlex
    URLs, reviewer flags and the eligibility rationale.
    """
    try:
        return store.candidate_detail(connection, candidate_id)
    except KeyError:
        raise HTTPException(404, f"no candidate {candidate_id!r}")


@app.get("/rubric")
def get_rubric() -> dict:
    """
    The approved rubric, in full, for an administrator or maintainer.

    Required by the revised policy: the classification criteria and the
    scoring values must be visible to whoever maintains them. A rubric
    nobody can read is a rubric nobody can contest, and every number
    below is a policy decision rather than a technical one.

    Read-only, and deliberately unauthenticated for now because the whole
    service is - see the authentication gap in KNOWN_DEFECTS.md. Nothing
    here is candidate data.
    """
    return {
        "version": scoring.RUBRIC_VERSION,
        "weights": dict(scoring.WEIGHTS),
        "categories": {name: list(keys)
                       for name, keys in scoring.CATEGORIES.items()},
        "category_totals": dict(scoring.CATEGORY_TOTALS),
        "saturation_points": {
            "publication_credit": scoring.FULL_MARKS_PUBLICATION_CREDIT,
            "teaching_years": scoring.FULL_MARKS_TEACHING_YEARS,
            "industry_years": scoring.FULL_MARKS_INDUSTRY_YEARS,
            "postdoctoral_years": scoring.FULL_MARKS_POSTDOC_YEARS,
            "citations": scoring.FULL_MARKS_CITATIONS,
        },
        "teaching_rank_credit": dict(roles.RANK_CREDIT),
        "assistantship_credit": scoring.ASSISTANTSHIP_CREDIT,
        "patents": {
            "credit_by_status": dict(schema.PATENT_CREDIT),
            "share_of_research_criterion": schema.PATENT_SHARE_OF_RESEARCH,
            "verification": schema.PATENT_VERIFICATION,
        },
        "degrees": degrees.visible_rubric(),
        "institutions": institutions.visible_rubric(),
        "eligibility_gates": [dict(g) for g in schema.ELIGIBILITY_GATES],
        "separation_of_concerns": (
            "Eligibility is a gate and never touches the score. Institution "
            "standing, qualification equivalence and branch relevance are "
            "three separate factors and are each applied once."),
        "approval_status": (
            "NOT APPROVED. Every value here is a DEMO/RESEARCH proposal for "
            "a student project and requires faculty sign-off before use in "
            "any real recruitment decision."),
    }


@app.get("/candidates/{candidate_id}/audit")
def get_audit(candidate_id: str,
              connection: sqlite3.Connection = Depends(get_db)) -> dict:
    """The raw audit record, including every intermediate stage output."""
    row = connection.execute(
        "SELECT audit_json FROM candidates WHERE candidate_id=?",
        (candidate_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"no candidate {candidate_id!r}")
    return json.loads(row["audit_json"])


@app.post("/candidates/{candidate_id}/explain")
async def generate_explanations(
    candidate_id: str,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Write prose for one candidate's already-computed criteria.

    Separated from the upload because it is the only stage that cannot
    affect a result. The model is handed the finished criterion - never
    the CV - and every number it writes is checked against the numbers it
    was given, exactly as in Stage 7. Prose that fails that check is
    replaced by the template, so this endpoint cannot introduce a claim
    the pipeline did not make.

    Idempotent in effect: calling it again regenerates the same text from
    the same inputs at temperature 0, and no score is touched either way.
    """
    row = connection.execute(
        "SELECT audit_json FROM candidates WHERE candidate_id=?",
        (candidate_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"no candidate {candidate_id!r}")

    scored = json.loads(row["audit_json"]).get("scoring", {})
    if not scored.get("criteria"):
        raise HTTPException(
            422, f"{candidate_id!r} has no stored criteria to explain")

    # Minutes of blocking generation - off the event loop, as with upload.
    try:
        explained = await run_in_threadpool(
            explain.explain_candidate, scored, use_llm=True)
    except Exception as exc:
        raise HTTPException(
            503, f"explanation generation failed: {type(exc).__name__}: {exc}")

    updated = store.update_explanations(connection, candidate_id, explained)

    return {
        "candidate_id": candidate_id,
        "criteria_updated": updated,
        "llm_used": explained.get("llm_used", False),
        "explanations_rejected": explained.get("explanations_rejected", 0),
        "state": store.explanation_state(connection, candidate_id),
        "explanations": explained.get("criterion_explanations", []),
        "note": ("Prose only. No score, measurement or eligibility outcome "
                 "was read from the model or changed by this call. Any "
                 "sentence that stated a number not present in the computed "
                 "criterion was rejected and replaced by a template."),
    }


@app.patch("/candidates/{candidate_id}/score")
def override_score(candidate_id: str, request: OverrideRequest,
                   connection: sqlite3.Connection = Depends(get_db)) -> dict:
    """
    Record a human override and return the updated state.

    The override is appended, never applied destructively: the computed
    figures stay exactly as the pipeline produced them, and the response
    shows both so a reviewer's number can never be mistaken for the
    pipeline's.
    """
    try:
        before = store.effective_state(connection, candidate_id)
    except KeyError:
        raise HTTPException(404, f"no candidate {candidate_id!r}")

    if request.field == "eligibility":
        original = before["effective_eligibility"]
        if request.new_value not in ("met", "not_met", "indeterminate"):
            raise HTTPException(
                422, "eligibility must be met, not_met or indeterminate")
    else:
        match = next((c for c in before["criteria"]
                      if c["criterion"] == request.field), None)
        if match is None:
            known = [c["criterion"] for c in before["criteria"]]
            raise HTTPException(
                422, f"unknown field {request.field!r}; expected 'eligibility' "
                     f"or one of {known}")
        original = match["effective_points"]
        if not isinstance(request.new_value, (int, float)):
            raise HTTPException(422, "a criterion override must be numeric")
        if not 0 <= float(request.new_value) <= match["weight"]:
            raise HTTPException(
                422, f"{request.field} must be between 0 and its weight "
                     f"({match['weight']})")

    try:
        override_id = store.record_override(
            connection, candidate_id, request.field,
            original_value=original, new_value=request.new_value,
            reason=request.reason, overridden_by=request.overridden_by)
    except ValueError as exc:                 # blank reason
        raise HTTPException(422, str(exc))
    except sqlite3.IntegrityError:            # unknown candidate
        raise HTTPException(404, f"no candidate {candidate_id!r}")

    after = store.effective_state(connection, candidate_id)
    return {
        "override_id": override_id,
        "applied": True,
        "computed_total": after["computed_total"],
        "effective_total": after["effective_total"],
        "computed_eligibility": after["computed_eligibility"],
        "effective_eligibility": after["effective_eligibility"],
        "changed": {
            "total": [before["effective_total"], after["effective_total"]],
            "eligibility": [before["effective_eligibility"],
                            after["effective_eligibility"]],
        },
        "candidate": after,
        "note": ("The computed figures are unchanged and remain the "
                 "pipeline's record. The override is applied on top and is "
                 "what the ranking now uses."),
    }


@app.get("/candidates/{candidate_id}/overrides")
def list_overrides(candidate_id: str,
                   connection: sqlite3.Connection = Depends(get_db)) -> dict:
    """The append-only override log for one candidate."""
    row = connection.execute(
        "SELECT 1 FROM candidates WHERE candidate_id=?",
        (candidate_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"no candidate {candidate_id!r}")
    return {"candidate_id": candidate_id,
            "overrides": store.overrides_for(connection, candidate_id)}
