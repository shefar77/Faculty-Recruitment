"""
STAGE 7b - SQLite persistence.

Deliberately thin. SQLite is the simplest thing that survives a restart for
a single-user project; it is NOT the source of truth. The audit JSON on
disk is. This table exists so the API (stage 8) can list and rank
candidates without re-reading every file, and so human overrides have
somewhere durable to live.

PRIVACY NOTE
  Rows contain real candidate names, emails and phone numbers, as the
  project requires - this is a recruitment system and the reviewer must
  know who they are assessing. The database file is therefore in
  .gitignore and must never be committed.

HUMAN OVERRIDES ARE APPEND-ONLY
  An override never rewrites a score. It is a new row recording the
  original value, the new value, who set it and why. The computed score
  stays visible underneath, because the point of the audit trail is to
  show that a human disagreed - not to erase what the pipeline said.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import summary  # the deterministic profile prose; no model call

BUSY_TIMEOUT_SECONDS = 10.0

DB_PATH = Path(__file__).resolve().parent.parent / "output" / "recruitment.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id     TEXT PRIMARY KEY,
    candidate_name   TEXT,
    source_file      TEXT,
    source_sha256    TEXT,
    total_score      REAL,
    max_score        REAL,
    eligibility      TEXT,
    eligibility_reason TEXT,
    rubric_version   TEXT,
    scoring_reliable INTEGER DEFAULT 1,
    unreliable_reasons TEXT,
    as_of_date       TEXT,
    scored_at        TEXT,
    audit_json       TEXT NOT NULL      -- the complete audit record
);

CREATE TABLE IF NOT EXISTS criteria (
    candidate_id     TEXT NOT NULL,
    criterion        TEXT NOT NULL,
    weight           REAL,
    measured         REAL,
    unit             TEXT,
    points           REAL,
    insufficient_evidence INTEGER,
    note             TEXT,
    explanation      TEXT,
    explanation_source TEXT,            -- 'llm' or 'template'
    PRIMARY KEY (candidate_id, criterion),
    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
);

-- Append-only. One row per override; nothing is ever updated in place.
CREATE TABLE IF NOT EXISTS overrides (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id     TEXT NOT NULL,
    field            TEXT NOT NULL,     -- criterion name, or 'eligibility'
    original_value   TEXT,
    new_value        TEXT,
    reason           TEXT NOT NULL,
    overridden_by    TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False because the API creates a connection per
    # request via a dependency, and FastAPI may run that dependency and the
    # endpoint on different threads. Each connection is still used by one
    # request at a time and closed with it - nothing is shared concurrently,
    # so the flag only switches off a thread-affinity assertion that does
    # not apply here.
    connection = sqlite3.connect(path, check_same_thread=False,
                                 timeout=BUSY_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row

    # WAL lets readers run concurrently with a writer. Without it, a
    # reviewer loading the ranked list blocks behind a save, and a save
    # blocks behind the list. Measured concurrency was already clean
    # without WAL - 120 simultaneous writes, no errors, integrity ok -
    # because every transaction here is milliseconds and the slow pipeline
    # work happens OUTSIDE the transaction. This is insurance against that
    # ceasing to be true, not a fix for an observed failure.
    #
    # Caveat: WAL requires the database to live on a local filesystem. It
    # is unsuitable over a network share, which is not a configuration
    # this project supports anyway.
    connection.execute("PRAGMA journal_mode = WAL")

    # Explicit, rather than relying on sqlite3's 5-second default: a
    # writer that finds the lock held retries for this long before raising
    # "database is locked".
    connection.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
    # SQLite ignores FOREIGN KEY clauses unless this is switched on, per
    # connection. Without it an override can be filed against a candidate
    # who does not exist - which a typo'd candidate_id would do silently,
    # leaving an audit entry attached to nothing.
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def save_audit(connection: sqlite3.Connection, audit: dict) -> None:
    """Upsert one candidate's audit record and its criteria."""
    scored = audit["scoring"]
    explained = audit.get("explanations", {})
    by_criterion = {e["criterion"]: e
                    for e in explained.get("criterion_explanations", [])}

    connection.execute(
        """INSERT INTO candidates (candidate_id, candidate_name, source_file,
                source_sha256, total_score, max_score, eligibility,
                eligibility_reason, rubric_version, scoring_reliable,
                unreliable_reasons, as_of_date, scored_at, audit_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(candidate_id) DO UPDATE SET
                candidate_name=excluded.candidate_name,
                source_file=excluded.source_file,
                source_sha256=excluded.source_sha256,
                total_score=excluded.total_score,
                max_score=excluded.max_score,
                eligibility=excluded.eligibility,
                eligibility_reason=excluded.eligibility_reason,
                rubric_version=excluded.rubric_version,
                scoring_reliable=excluded.scoring_reliable,
                unreliable_reasons=excluded.unreliable_reasons,
                as_of_date=excluded.as_of_date,
                scored_at=excluded.scored_at,
                audit_json=excluded.audit_json""",
        (
            audit["candidate_id"],
            scored.get("candidate_name"),
            audit.get("source", {}).get("file"),
            audit.get("source", {}).get("sha256"),
            scored.get("total_score"),
            scored.get("max_score"),
            scored.get("eligibility", {}).get("outcome"),
            scored.get("eligibility", {}).get("reason"),
            scored.get("rubric", {}).get("version"),
            1 if scored.get("scoring_reliable", True) else 0,
            json.dumps(scored.get("unreliable_reasons", [])),
            scored.get("as_of_date"),
            scored.get("scored_at"),
            json.dumps(audit, ensure_ascii=False),
        ),
    )

    for criterion in scored.get("criteria", []):
        explanation = by_criterion.get(criterion["criterion"], {})
        connection.execute(
            """INSERT INTO criteria (candidate_id, criterion, weight, measured,
                    unit, points, insufficient_evidence, note, explanation,
                    explanation_source)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(candidate_id, criterion) DO UPDATE SET
                    weight=excluded.weight, measured=excluded.measured,
                    unit=excluded.unit, points=excluded.points,
                    insufficient_evidence=excluded.insufficient_evidence,
                    note=excluded.note, explanation=excluded.explanation,
                    explanation_source=excluded.explanation_source""",
            (
                audit["candidate_id"],
                criterion["criterion"],
                criterion.get("weight"),
                criterion.get("measured") if isinstance(
                    criterion.get("measured"), (int, float)) else None,
                criterion.get("unit"),
                criterion.get("points"),
                1 if criterion.get("insufficient_evidence") else 0,
                criterion.get("note"),
                explanation.get("explanation"),
                explanation.get("source"),
            ),
        )
    connection.commit()


def update_explanations(connection: sqlite3.Connection, candidate_id: str,
                        explained: dict) -> int:
    """
    Replace the stored prose for one candidate. Touches NO number.

    Explanations are generated after the fact rather than during upload,
    because they are the one part of the pipeline that cannot change a
    result: the model is handed the finished criterion and writes sentences
    about it. Keeping them off the critical path costs a reviewer nothing
    and saves roughly a quarter of the run.

    Only the `explanation` and `explanation_source` columns are written.
    The scores, measurements and notes in the same row are left exactly as
    the pipeline computed them, so this cannot quietly restate a number.
    The audit record is updated in step, so the file and the table cannot
    disagree about what was said.
    """
    row = connection.execute(
        "SELECT audit_json FROM candidates WHERE candidate_id=?",
        (candidate_id,)).fetchone()
    if row is None:
        raise KeyError(candidate_id)

    by_criterion = {entry["criterion"]: entry
                    for entry in explained.get("criterion_explanations", [])}

    updated = 0
    for criterion, entry in by_criterion.items():
        cursor = connection.execute(
            """UPDATE criteria SET explanation=?, explanation_source=?
               WHERE candidate_id=? AND criterion=?""",
            (entry.get("explanation"), entry.get("source"),
             candidate_id, criterion))
        updated += cursor.rowcount

    audit = json.loads(row["audit_json"])
    audit["explanations"] = explained
    connection.execute(
        "UPDATE candidates SET audit_json=? WHERE candidate_id=?",
        (json.dumps(audit, ensure_ascii=False), candidate_id))

    connection.commit()
    return updated


def explanation_state(connection: sqlite3.Connection,
                      candidate_id: str) -> dict:
    """
    Whether this candidate's prose has been generated yet.

    'template' means the deterministic sentence written by Python, which is
    always present and always correct. 'llm' means a model wrote it and it
    passed the numeric check. The UI uses this to decide whether offering
    to generate prose would do anything.
    """
    rows = connection.execute(
        "SELECT criterion, explanation_source FROM criteria "
        "WHERE candidate_id=?", (candidate_id,)).fetchall()
    if not rows:
        raise KeyError(candidate_id)
    sources = [r["explanation_source"] for r in rows]
    return {
        "criteria_total": len(sources),
        "llm_written": sum(1 for s in sources if s == "llm"),
        "template_written": sum(1 for s in sources if s != "llm"),
        "all_llm": all(s == "llm" for s in sources),
    }


def record_override(connection: sqlite3.Connection, candidate_id: str,
                    field: str, original_value, new_value, reason: str,
                    overridden_by: str = "faculty") -> int:
    """
    Append an override. Never modifies the computed score.

    A reason is mandatory: an unexplained override is indistinguishable
    from a mistake when someone reads the log six months later.
    """
    if not reason or not reason.strip():
        raise ValueError("an override requires a reason")

    cursor = connection.execute(
        """INSERT INTO overrides (candidate_id, field, original_value,
                new_value, reason, overridden_by, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (candidate_id, field, json.dumps(original_value),
         json.dumps(new_value), reason.strip(), overridden_by,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    connection.commit()
    return cursor.lastrowid


def effective_state(connection: sqlite3.Connection, candidate_id: str) -> dict:
    """
    The two values a reviewer must see side by side: what the pipeline
    COMPUTED, and what is EFFECTIVE after human overrides.

    The computed figures are immutable - they are never updated by an
    override, and re-running the pipeline recomputes them from evidence.
    Overrides live in their own append-only table and are applied here, on
    read. So:

      * the original is preserved and always retrievable;
      * the override is separately visible, with its reason and author;
      * AND the override genuinely takes effect - it changes the effective
        score and therefore the ranking.

    All three at once. An override that changed nothing would be a sticky
    note, not a decision, and the whole point of human-final-decision is
    that the human's judgement wins.

    Where a field has been overridden more than once, the latest wins and
    the earlier ones remain in the history.
    """
    candidate = connection.execute(
        "SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)
    ).fetchone()
    if candidate is None:
        raise KeyError(candidate_id)

    criteria = connection.execute(
        "SELECT * FROM criteria WHERE candidate_id=? ORDER BY criterion",
        (candidate_id,)).fetchall()
    history = overrides_for(connection, candidate_id)

    # Latest override per field.
    latest: dict[str, dict] = {}
    for entry in history:
        latest[entry["field"]] = entry

    # The effective total is the COMPUTED total plus the deltas of any
    # overridden criteria - not a fresh sum of the criterion rows.
    #
    # That guarantees effective == computed exactly when nothing has been
    # overridden, which a re-sum does not: if a criterion row were ever
    # missing or stale, re-summing would silently report a different total
    # than the pipeline produced and attribute it to no one.
    effective_criteria = []
    delta = 0.0

    for row in criteria:
        computed_points = row["points"] or 0.0
        override = latest.get(row["criterion"])
        points = computed_points
        if override is not None:
            try:
                points = float(json.loads(override["new_value"]))
            except (TypeError, ValueError):
                points = computed_points
            delta += points - computed_points
        effective_criteria.append({
            "criterion": row["criterion"],
            "weight": row["weight"],
            "computed_points": computed_points,
            "effective_points": points,
            "overridden": override is not None,
            "override": _override_summary(override),
            "measured": row["measured"],
            "unit": row["unit"],
            "insufficient_evidence": bool(row["insufficient_evidence"]),
            "note": row["note"],
            "explanation": row["explanation"],
            "explanation_source": row["explanation_source"],
        })

    eligibility_override = latest.get("eligibility")
    effective_eligibility = candidate["eligibility"]
    if eligibility_override is not None:
        try:
            effective_eligibility = json.loads(eligibility_override["new_value"])
        except (TypeError, ValueError):
            pass

    return {
        "candidate_id": candidate_id,
        "candidate_name": candidate["candidate_name"],
        "max_score": candidate["max_score"],

        # Immutable, straight from the pipeline.
        "computed_total": candidate["total_score"],
        "computed_eligibility": candidate["eligibility"],
        "computed_eligibility_reason": candidate["eligibility_reason"],

        # What the reviewer's decisions make of it.
        "effective_total": round((candidate["total_score"] or 0.0) + delta, 2),
        "effective_eligibility": effective_eligibility,

        "is_overridden": bool(latest),
        "overridden_fields": sorted(latest),
        "criteria": effective_criteria,
        "override_history": history,
        "rubric_version": candidate["rubric_version"],
        "as_of_date": candidate["as_of_date"],

        # False when a lookup service was unreachable during scoring. The
        # number is still present because the arithmetic needs one, but it
        # must not be read as a judgement on the candidate.
        "scoring_reliable": bool(
            candidate["scoring_reliable"]
            if "scoring_reliable" in candidate.keys() else 1),
        "unreliable_reasons": json.loads(
            candidate["unreliable_reasons"]
            if "unreliable_reasons" in candidate.keys()
            and candidate["unreliable_reasons"] else "[]"),
    }


def _override_summary(override) -> dict | None:
    if override is None:
        return None
    return {
        "id": override["id"],
        "original_value": json.loads(override["original_value"]),
        "new_value": json.loads(override["new_value"]),
        "reason": override["reason"],
        "overridden_by": override["overridden_by"],
        "created_at": override["created_at"],
    }


def ranked_candidates(connection: sqlite3.Connection) -> list[dict]:
    """
    Eligible candidates first, then everyone else, each by score.

    Gated candidates are NOT dropped and NOT interleaved - the two groups
    mean different things (DESIGN_DECISIONS.md section 3).

    Ranking uses the EFFECTIVE values, so a human override actually moves
    a candidate. Each row still carries the computed figures beside them,
    so the UI can always show "computed X, overridden to Y" rather than
    silently presenting a human number as if the pipeline produced it.
    """
    ids = [row["candidate_id"] for row in connection.execute(
        "SELECT candidate_id FROM candidates").fetchall()]
    states = [effective_state(connection, candidate_id) for candidate_id in ids]

    states.sort(key=lambda s: (
        0 if s["effective_eligibility"] == "met" else 1,
        -(s["effective_total"] or 0.0),
    ))
    return states


# Per-criterion fields the reviewer needs in order to see WHY a number is
# what it is. They live in the audit record rather than the criteria
# table: that table stores what each score IS, and widening it for every
# new kind of evidence would mean a migration each time.
#
# Deliberately excludes points, weight, measured and eligibility.
# effective_state() owns every figure, and an audit record that disagreed
# with the stored score must not be able to overwrite one here - a display
# merge is not allowed to change an outcome.
EVIDENCE_FIELDS = (
    "evidence",
    "unscored_positions",
    "unclassified_roles",
    "indeterminate",
    "indeterminate_entries",
    "publication_credit",
    "patent_credit",
    "patent_credit_counted",
    # Revised rubric: which approved category a criterion rolls up into,
    # the named multipliers behind an education score, and the reasons a
    # criterion wants a human to look at it.
    "category",
    "factors",
    "needs_review",
    "review_reasons",
)


def candidate_detail(connection: sqlite3.Connection, candidate_id: str) -> dict:
    """
    Everything a reviewer needs for one candidate, assembled here rather
    than in the API layer.

    effective_state() covers scores and overrides. This adds the evidence
    that lives in the stored audit record - publication verdicts with
    their OpenAlex URLs, the roles and papers behind each criterion,
    reviewer flags, the eligibility rationale and the run's provenance -
    so the HTTP layer only has to serialise a dict.
    """
    state = effective_state(connection, candidate_id)

    row = connection.execute(
        "SELECT audit_json FROM candidates WHERE candidate_id=?",
        (candidate_id,)).fetchone()
    audit = json.loads(row["audit_json"]) if row and row["audit_json"] else {}
    scored = audit.get("scoring", {})

    # The credit each entry actually earned, from the scored criterion.
    # score_publications emits one row per verified entry, in the same
    # order, before any patent rows - so position is the join key.
    scored_rows = [r for r in next(
        (c.get("evidence") or [] for c in scored.get("criteria", [])
         if c.get("criterion") == "publications"), [])
        if r.get("kind") != "patent"]

    publications = []
    for index, entry in enumerate(
            audit.get("verification", {}).get("publications", [])):
        verification = entry.get("verification", {})
        credited = scored_rows[index] if index < len(scored_rows) else {}
        publications.append({
            "credit": credited.get("credit"),
            "credit_reason": credited.get("reason"),
            "review_note": credited.get("note"),
            "validation_verdict": entry.get("validation_verdict"),
            "duplicate_of": entry.get("duplicate_of"),
            "resolved_by": verification.get("resolved_by"),
            "doi_note": verification.get("doi_note"),
            "title": entry.get("title"),
            "status": entry.get("status"),
            "outcome": verification.get("outcome"),
            "title_similarity": verification.get("title_similarity"),
            "author_match": verification.get("author_match"),
            "openalex_id": verification.get("openalex_id"),
            "query_url": verification.get("query_url"),
            "matched_title": verification.get("matched_title"),
            "cited_by_count": verification.get("cited_by_count"),
            "credit_exclusions": entry.get("credit_exclusions", []),
            "note": verification.get("note"),
        })

    explanations = {
        item["criterion"]: item
        for item in audit.get("explanations", {})
                         .get("criterion_explanations", [])
    }
    audited = {
        item["criterion"]: item for item in scored.get("criteria", [])
    }
    for criterion in state["criteria"]:
        explanation = explanations.get(criterion["criterion"], {})
        criterion["explanation_source"] = explanation.get(
            "source", criterion.get("explanation_source"))

        record = audited.get(criterion["criterion"], {})
        for field in EVIDENCE_FIELDS:
            if field in record:
                criterion[field] = record[field]

    state.update({
        "eligibility": scored.get("eligibility", {}),
        # Assembled from the stored audit record at read time, not during
        # upload: it costs microseconds, it needs no model call, and a
        # candidate scored before this existed gets one without a re-run.
        "profile_summary": summary.build_summary(audit),
        # Category subtotals come from the stored audit record rather than
        # being re-summed here: the pipeline's own arithmetic is the
        # record, and a second implementation of it could disagree.
        "category_scores": scored.get("category_scores", {}),
        "rubric_department": scored.get("rubric", {}).get("department"),
        "explanation_state": explanation_state(connection, candidate_id),
        "reviewer_flags": scored.get("reviewer_flags", []),
        "eligibility_explanation": audit.get("explanations", {})
                                        .get("eligibility_explanation"),
        "publications": publications,
        "source": audit.get("source", {}),
        "models": audit.get("models", {}),
        "rubric": scored.get("rubric", {}),
        "disclaimer": audit.get("disclaimer"),
        "human_decision_required": True,
    })
    return state


def overrides_for(connection: sqlite3.Connection, candidate_id: str) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM overrides WHERE candidate_id=? ORDER BY created_at",
        (candidate_id,)).fetchall()
    return [dict(row) for row in rows]
