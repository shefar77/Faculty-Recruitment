"""
STAGE 7 TESTS - explanation guard rails and the SQLite store.

The explanation tests never call the model: they feed prose directly to the
checker, which is the component that actually enforces the rule. Prompt
wording has failed on every rule that mattered in this project, so what is
tested here is the code that catches the model, not the model.

Run:  python -m pytest tests/ -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import explain  # noqa: E402
import store    # noqa: E402

CRITERION = {
    "criterion": "teaching_experience",
    "weight": 25,
    "measured": 2.17,
    "unit": "years (overlapping merged; assistantships at 0.5)",
    "fraction_of_weight": 0.434,
    "points": 10.85,
    "source_fields": ["positions[].category", "positions[].start_date"],
    "evidence": [{"title": "Assistant Professor", "months": 15}],
    "insufficient_evidence": False,
    "note": "full marks at 5.0 years",
    "unclassified_roles": [],
}


# ---------------------------------------------------------------------------
# The explanation checker - the LLM's leash
# ---------------------------------------------------------------------------

def test_faithful_explanation_is_accepted():
    text = ("The teaching experience criterion scored 10.85 out of 25. "
            "It reflects 2.17 measured years. The figure comes from the "
            "recorded position dates. Nothing was inferred.")
    ok, why = explain.check_explanation(text, CRITERION)
    assert ok, why


def test_invented_number_is_rejected():
    """The failure that matters: a number that appears from nowhere."""
    text = ("The teaching experience criterion scored 10.85 out of 25, "
            "across 7 separate roles.")
    ok, why = explain.check_explanation(text, CRITERION)
    assert not ok
    assert "not present in the data" in why


def test_altered_number_is_rejected():
    text = "The teaching experience criterion scored 12.40 out of 25."
    ok, why = explain.check_explanation(text, CRITERION)
    assert not ok
    assert "12.4" in why


def test_sensible_rounding_is_allowed():
    """10.85 read aloud as 10.9 is not a new number."""
    text = ("The teaching experience criterion scored about 10.9 out of 25. "
            "It reflects roughly 2 years of teaching.")
    ok, why = explain.check_explanation(text, CRITERION)
    assert ok, why


def test_hiring_recommendation_is_rejected():
    """The model describes; the human decides."""
    for verdict in ("This candidate should be hired.",
                    "I recommend rejecting this applicant.",
                    "The candidate is not qualified for the post."):
        text = f"The criterion scored 10.85 out of 25. {verdict}"
        ok, why = explain.check_explanation(text, CRITERION)
        assert not ok, f"allowed a verdict: {verdict}"


def test_empty_explanation_is_rejected():
    ok, why = explain.check_explanation("   ", CRITERION)
    assert not ok


def test_rejected_prose_falls_back_to_a_template():
    """A verified plain sentence beats unverified fluent prose."""
    text = explain.template_explanation(CRITERION)
    ok, why = explain.check_explanation(text, CRITERION)
    assert ok, f"the fallback must itself pass the check: {why}"
    assert "10.85" in text and "25" in text


def test_template_for_missing_evidence_is_neutrally_worded():
    criterion = dict(CRITERION, points=0.0, measured=0.0,
                     insufficient_evidence=True,
                     note="no teaching position with a readable date range")
    text = explain.template_explanation(criterion)
    assert "did not provide evidence" in text
    for blaming in ("failed to", "lacks", "deficient", "poor"):
        assert blaming not in text.lower()


def test_explain_criterion_without_llm_uses_template():
    result = explain.explain_criterion(CRITERION, use_llm=False)
    assert result["source"] == "template"
    assert result["explanation"]


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------

def _audit(candidate_id="Candidate_TEST", score=42.5, eligibility="met"):
    return {
        "candidate_id": candidate_id,
        "source": {"file": f"{candidate_id}.pdf", "sha256": "abc123"},
        "scoring": {
            "candidate_name": "Test Person",
            "total_score": score,
            "max_score": 100,
            "eligibility": {"outcome": eligibility, "reason": "because"},
            "rubric": {"version": "demo-rubric-v1"},
            "as_of_date": "2026-10-15",
            "scored_at": "2026-10-15T00:00:00+00:00",
            "criteria": [dict(CRITERION)],
        },
        "explanations": {
            "criterion_explanations": [
                {"criterion": "teaching_experience",
                 "explanation": "Scored 10.85 out of 25.",
                 "source": "template"}
            ]
        },
    }


def test_audit_round_trips_through_sqlite(tmp_path):
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit())

    row = connection.execute("SELECT * FROM candidates").fetchone()
    assert row["candidate_id"] == "Candidate_TEST"
    assert row["total_score"] == 42.5
    # The complete audit record is retrievable, not just the summary.
    assert json.loads(row["audit_json"])["scoring"]["total_score"] == 42.5

    criterion_row = connection.execute("SELECT * FROM criteria").fetchone()
    assert criterion_row["points"] == 10.85
    assert criterion_row["explanation_source"] == "template"


def test_rerunning_a_candidate_updates_rather_than_duplicates(tmp_path):
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit(score=42.5))
    store.save_audit(connection, _audit(score=51.0))

    rows = connection.execute("SELECT * FROM candidates").fetchall()
    assert len(rows) == 1
    assert rows[0]["total_score"] == 51.0


def test_override_is_appended_and_never_rewrites_the_score(tmp_path):
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit(score=42.5))

    store.record_override(connection, "Candidate_TEST", "teaching_experience",
                          original_value=10.85, new_value=20.0,
                          reason="Verified two further years by phone.",
                          overridden_by="Prof. Reviewer")

    overrides = store.overrides_for(connection, "Candidate_TEST")
    assert len(overrides) == 1
    assert json.loads(overrides[0]["original_value"]) == 10.85
    assert json.loads(overrides[0]["new_value"]) == 20.0
    assert overrides[0]["reason"]

    # The computed score is untouched - the override sits alongside it.
    row = connection.execute(
        "SELECT total_score FROM candidates").fetchone()
    assert row["total_score"] == 42.5


def test_overrides_accumulate_rather_than_replace(tmp_path):
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit())
    store.record_override(connection, "Candidate_TEST", "eligibility",
                          "indeterminate", "met", reason="Award letter seen.")
    store.record_override(connection, "Candidate_TEST", "eligibility",
                          "met", "indeterminate", reason="Letter was for a different degree.")
    assert len(store.overrides_for(connection, "Candidate_TEST")) == 2


def test_override_requires_a_reason(tmp_path):
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit())
    for empty in ("", "   ", None):
        try:
            store.record_override(connection, "Candidate_TEST", "x", 1, 2,
                                  reason=empty)
        except ValueError:
            continue
        raise AssertionError(f"accepted an override with reason={empty!r}")


def test_ranking_puts_eligible_first_and_never_interleaves(tmp_path):
    connection = store.connect(tmp_path / "t.db")
    # A gated candidate scoring higher than an eligible one.
    store.save_audit(connection, _audit("HighButGated", 90.0, "indeterminate"))
    store.save_audit(connection, _audit("LowButEligible", 11.0, "met"))
    store.save_audit(connection, _audit("MidEligible", 55.0, "met"))

    ranked = [r["candidate_id"] for r in store.ranked_candidates(connection)]
    assert ranked == ["MidEligible", "LowButEligible", "HighButGated"], (
        "a high-scoring gated candidate must not outrank an eligible one")


def test_fraction_restated_as_a_percentage_is_allowed():
    """
    0.434 of the weight read aloud as "43%" is a unit change, not a new
    number. Only values already in 0..1 get this licence, so it cannot
    excuse arbitrary arithmetic.
    """
    criterion = dict(CRITERION, fraction_of_weight=0.4, points=10.0)
    text = ("The teaching experience criterion scored 10.0 out of 25. "
            "That is 40% of the available weight. It reflects 2.17 years.")
    ok, why = explain.check_explanation(text, criterion)
    assert ok, why


def test_percentage_licence_does_not_permit_arbitrary_arithmetic():
    criterion = dict(CRITERION, fraction_of_weight=0.4, points=10.0)
    text = "The criterion scored 10.0 out of 25, ranking 3rd of 17 applicants."
    ok, why = explain.check_explanation(text, criterion)
    assert not ok
    assert "17" in why


def test_override_for_an_unknown_candidate_is_refused(tmp_path):
    """
    SQLite ignores foreign keys unless PRAGMA foreign_keys is on. Without
    it, a typo'd candidate_id files an audit entry against nobody.
    """
    import sqlite3
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit("Candidate_REAL"))
    try:
        store.record_override(connection, "Candidate_TYPO", "eligibility",
                              "indeterminate", "met", reason="oops")
    except sqlite3.IntegrityError:
        return
    raise AssertionError("accepted an override for a non-existent candidate")


# ---------------------------------------------------------------------------
# Claims about numbers, which a numeric check cannot see
# ---------------------------------------------------------------------------

def test_asserting_a_paper_does_not_exist_is_rejected():
    """
    "Not found" means we could not corroborate it, never that the work is
    fictitious (DESIGN_DECISIONS.md section 6). The real model produced
    exactly this overreach unprompted.
    """
    criterion = dict(CRITERION, criterion="publications", points=0.0,
                     insufficient_evidence=True)
    for overreach in (
        "The search found no evidence of its existence.",
        "The candidate never published this paper.",
        "This appears to be a fabricated entry.",
    ):
        text = f"The publications criterion scored 0.0 out of 25. {overreach}"
        ok, why = explain.check_explanation(text, criterion)
        assert not ok, f"allowed an overreach: {overreach}"
        assert "non-existence or dishonesty" in why


def test_describing_a_failed_lookup_neutrally_is_accepted():
    criterion = dict(CRITERION, criterion="publications", points=0.0,
                     insufficient_evidence=True)
    text = ("The publications criterion scored 0.0 out of 25. The listed "
            "paper could not be corroborated against the index searched. "
            "That is a limit on what could be confirmed here.")
    ok, why = explain.check_explanation(text, criterion)
    assert ok, why


def test_false_claim_of_full_marks_is_rejected():
    """Every number real, the claim about them false."""
    criterion = dict(CRITERION, points=10.0, weight=25)
    text = ("The teaching experience criterion scored 10.0 out of 25, "
            "corresponding to the full points awarded for this criterion.")
    ok, why = explain.check_explanation(text, criterion)
    assert not ok
    assert "claims" in why and "10.0 of 25" in why


def test_true_claim_of_full_marks_is_accepted():
    criterion = dict(CRITERION, points=25.0, weight=25)
    text = ("The teaching experience criterion scored 25.0 out of 25, "
            "which is full marks for this criterion.")
    ok, why = explain.check_explanation(text, criterion)
    assert ok, why


def test_threshold_wording_is_not_mistaken_for_a_claim():
    """
    "full marks at 5.0 years" states the SATURATION POINT; "the full marks
    awarded" claims to have reached it. The criterion's own note uses the
    first form, and the template quotes it, so confusing the two made the
    fallback fail its own check.
    """
    criterion = dict(CRITERION, points=10.85, weight=25,
                     note="full marks at 5.0 years")
    text = explain.template_explanation(criterion)
    ok, why = explain.check_explanation(text, criterion)
    assert ok, why
    assert "full marks at" in text


# ---------------------------------------------------------------------------
# Override semantics: original immutable, override visible, AND effective
# ---------------------------------------------------------------------------

def test_no_override_means_effective_equals_computed(tmp_path):
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit(score=42.5))
    state = store.effective_state(connection, "Candidate_TEST")
    assert state["computed_total"] == 42.5
    assert state["effective_total"] == 42.5
    assert state["is_overridden"] is False


def test_override_preserves_the_original_and_takes_effect(tmp_path):
    """
    All three properties at once - this is the thing to get right:
      1. the computed value is immutable and still retrievable;
      2. the override is separately visible with reason and author;
      3. the override ACTUALLY changes the effective score.
    """
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit(score=42.5))   # criterion = 10.85
    store.record_override(connection, "Candidate_TEST", "teaching_experience",
                          original_value=10.85, new_value=20.85,
                          reason="Two further years confirmed at interview.",
                          overridden_by="Prof. Reviewer")

    state = store.effective_state(connection, "Candidate_TEST")

    # 1. original preserved
    assert state["computed_total"] == 42.5
    criterion = state["criteria"][0]
    assert criterion["computed_points"] == 10.85

    # 2. override separately visible
    assert criterion["overridden"] is True
    assert criterion["override"]["original_value"] == 10.85
    assert criterion["override"]["new_value"] == 20.85
    assert criterion["override"]["reason"]
    assert criterion["override"]["overridden_by"] == "Prof. Reviewer"

    # 3. it actually takes effect: +10.00 on the criterion, +10.00 on total
    assert criterion["effective_points"] == 20.85
    assert state["effective_total"] == 52.5
    assert state["is_overridden"] is True


def test_override_changes_the_ranking(tmp_path):
    """An override that could not reorder the list would be a sticky note."""
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit("Alpha", 40.0, "met"))
    store.save_audit(connection, _audit("Beta", 30.0, "met"))
    assert [r["candidate_id"] for r in store.ranked_candidates(connection)] \
        == ["Alpha", "Beta"]

    store.record_override(connection, "Beta", "teaching_experience",
                          original_value=10.85, new_value=30.85,
                          reason="Additional teaching record supplied.")

    ranked = store.ranked_candidates(connection)
    assert [r["candidate_id"] for r in ranked] == ["Beta", "Alpha"]
    beta = ranked[0]
    assert beta["computed_total"] == 30.0      # original still shown
    assert beta["effective_total"] == 50.0     # and the override applied


def test_eligibility_override_moves_a_gated_candidate_into_the_eligible_group(tmp_path):
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit("Gated", 90.0, "indeterminate"))
    store.save_audit(connection, _audit("Eligible", 50.0, "met"))
    assert [r["candidate_id"] for r in store.ranked_candidates(connection)] \
        == ["Eligible", "Gated"]

    store.record_override(connection, "Gated", "eligibility",
                          "indeterminate", "met",
                          reason="Award letter sighted; degree conferred.")

    ranked = store.ranked_candidates(connection)
    assert [r["candidate_id"] for r in ranked] == ["Gated", "Eligible"]
    assert ranked[0]["computed_eligibility"] == "indeterminate"   # preserved
    assert ranked[0]["effective_eligibility"] == "met"            # applied


def test_latest_override_wins_and_history_is_kept(tmp_path):
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit(score=42.5))
    store.record_override(connection, "Candidate_TEST", "teaching_experience",
                          10.85, 20.0, reason="First correction.")
    store.record_override(connection, "Candidate_TEST", "teaching_experience",
                          20.0, 15.0, reason="Revised after checking the letter.")

    state = store.effective_state(connection, "Candidate_TEST")
    assert state["criteria"][0]["effective_points"] == 15.0
    assert len(state["override_history"]) == 2       # nothing is lost
    assert state["computed_total"] == 42.5           # still immutable


def test_rerunning_the_pipeline_does_not_erase_overrides(tmp_path):
    """Re-scoring recomputes the pipeline's numbers; the human's stand."""
    connection = store.connect(tmp_path / "t.db")
    store.save_audit(connection, _audit(score=42.5))
    store.record_override(connection, "Candidate_TEST", "teaching_experience",
                          10.85, 20.85, reason="Confirmed at interview.")
    store.save_audit(connection, _audit(score=44.0))   # pipeline re-run

    state = store.effective_state(connection, "Candidate_TEST")
    assert state["computed_total"] == 44.0
    assert state["effective_total"] == 54.0
    assert len(state["override_history"]) == 1


# ---------------------------------------------------------------------------
# "full marks": a CLAIM is rejected, a THRESHOLD mention is not
# ---------------------------------------------------------------------------
# Real model output. An earlier guard rejected three accurate explanations
# because it matched the words without reading the context around them.

def test_threshold_mentions_of_full_marks_are_accepted():
    teaching = dict(CRITERION, points=10.0, weight=25, measured=2.0,
                    fraction_of_weight=0.4, note="full marks at 5.0 years")
    accurate = [
        "The candidate has 2.0 years of teaching experience, which is 40% of "
        "the 5.0-year threshold for full marks. This scores 10.0 out of 25.",
        "This amounts to 10.0 points out of 25 available, which is less than "
        "the full marks threshold. The measured figure was 2.0 years.",
        "Reaching full marks would require 5.0 years. The measured 2.0 years "
        "scores 10.0 out of 25.",
    ]
    for text in accurate:
        ok, why = explain.check_explanation(text, teaching)
        assert ok, f"rejected an accurate threshold statement: {why}"


def test_claiming_full_marks_without_them_is_still_rejected():
    """The true positive must survive the false-positive fix."""
    publications = dict(CRITERION, criterion="publications", points=38.0,
                        weight=40, measured=19.0, fraction_of_weight=0.95)
    text = ("The candidate has 19 credited publications, which is 95% of the "
            "weight. This score is 38 out of 40 available, earning full marks "
            "for this criterion.")
    ok, why = explain.check_explanation(text, publications)
    assert not ok
    assert "38.0 of 40" in why


def test_claiming_full_marks_when_they_were_earned_is_accepted():
    maxed = dict(CRITERION, points=25.0, weight=25)
    text = ("The criterion scored 25.0 out of 25, earning full marks. "
            "The measured value reached the threshold. Nothing was inferred.")
    ok, why = explain.check_explanation(text, maxed)
    assert ok, why


def test_concurrent_writers_do_not_lose_or_corrupt_data(tmp_path):
    """
    Many threads writing one SQLite file at once, mixing short override
    inserts with the long multi-row save_audit transaction - the realistic
    worst case of an upload landing while reviewers click override.
    """
    import threading

    db = tmp_path / "concurrent.db"
    setup = store.connect(db)
    for i in range(4):
        store.save_audit(setup, _audit(f"C{i}", 50.0, "met"))
    setup.close()

    writers, rounds = 12, 10
    errors: list[str] = []
    overrides_written = 0
    lock = threading.Lock()
    barrier = threading.Barrier(writers)

    def worker(worker_id: int):
        nonlocal overrides_written
        barrier.wait()                       # collide, do not queue
        for round_index in range(rounds):
            connection = store.connect(db)
            try:
                candidate = f"C{(worker_id + round_index) % 4}"
                if worker_id % 3 == 0:
                    store.save_audit(connection, _audit(candidate, 60.0, "met"))
                else:
                    store.record_override(
                        connection, candidate, "teaching_experience",
                        10.85, 11.0, reason=f"w{worker_id} r{round_index}")
                    with lock:
                        overrides_written += 1
            except Exception as exc:
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [], f"concurrent writes failed: {errors[:3]}"

    check = store.connect(db)
    rows = check.execute("SELECT COUNT(*) n FROM overrides").fetchone()["n"]
    integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    journal = check.execute("PRAGMA journal_mode").fetchone()[0]
    check.close()

    assert rows == overrides_written, "a write was lost"
    assert integrity == "ok"
    assert journal.lower() == "wal"
