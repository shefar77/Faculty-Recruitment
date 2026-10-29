"""
STAGE 6 TESTS - deterministic scoring.

Every expected number here is hand-calculable from the rubric, and the
arithmetic is written out in the test so a reader can check it without
running anything. If a weight changes, these fail loudly - which is the
point.

Run:  python -m pytest tests/ -v
"""

from __future__ import annotations

import sys

import pytest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import schema   # noqa: E402
import scoring  # noqa: E402

AS_OF = date(2026, 10, 15)      # fixed, so "Present" never drifts


def wrap(value, evidence="evidence text"):
    return {"value": value, "evidence": evidence}


def validated(profile=None, positions=None, summary=None):
    profile = dict(profile or {})
    profile["positions"] = positions or []
    return {
        "candidate_id": "Candidate_TEST",
        "profile": profile,
        "validation_summary": summary or {},
    }


def verified(pubs=None):
    return {"candidate_id": "Candidate_TEST", "publications": pubs or []}


def pub(title, status, outcome, citations=0, exclusions=None):
    return {
        "title": title,
        "status": status,
        "credit_exclusions": exclusions or [],
        "verification": {
            "outcome": outcome,
            "cited_by_count": citations,
            "openalex_id": "https://openalex.org/W1",
            "query_url": "https://api.openalex.org/works?search=x",
        },
    }


def criterion(result, name):
    return next(c for c in result["criteria"] if c["criterion"] == name)


# ---------------------------------------------------------------------------
# Determinism - the property the whole stage exists for
# ---------------------------------------------------------------------------

def test_same_input_gives_same_output():
    v = validated({"phd_status": wrap("awarded"), "phd_year": wrap(2020)},
                  [{"title": "Lecturer", "category": "teaching",
                    "start_date": "2020-01", "end_date": "2023-01",
                    "is_current": False}])
    w = verified([pub("A", "published", "matched", citations=10)])

    first = scoring.score_candidate(v, w, as_of=AS_OF)
    second = scoring.score_candidate(v, w, as_of=AS_OF)

    assert first["total_score"] == second["total_score"]
    assert [c["points"] for c in first["criteria"]] == \
           [c["points"] for c in second["criteria"]]


def test_weights_sum_to_one_hundred():
    assert sum(scoring.WEIGHTS.values()) == 100


# ---------------------------------------------------------------------------
# Hand-calculable totals
# ---------------------------------------------------------------------------

def test_hand_calculated_total():
    """
    Worked by hand:
      publications: 5 matched x 1.00 = 5.0 credit; 5/20 saturation = 0.25
                    -> 0.25 x 40 = 10.00
      teaching:     2020-01 to 2023-01 = 36 months = 3.0 yr; 3/5 = 0.6
                    -> 0.6 x 20 = 12.00   ("Lecturer" = base rank, x1.0)
      doctorate:    awarded, institution not named -> unknown tier x0.7
                    -> 1.0 x 0.7 x 15 = 10.50
      postdoctoral: none -> 0.00
      masters:      none stated -> 0.00
      bachelors:    none stated -> 0.00
      industry:     none -> 0.00
      visibility:   5 papers x 10 citations = 50; 50/100 = 0.5
                    -> 0.5 x 5 = 2.50
      TOTAL = 8.75 + 12 + 10.5 + 0 + 0 + 0 + 0 + 2.5 = 33.75

    Teaching here is a "Lecturer", classified as a faculty appointment, so
    it counts at the full per-month rate.
    """
    v = validated({"phd_status": wrap("awarded"), "phd_year": wrap(2020)},
                  [{"title": "Lecturer", "category": "teaching",
                    "start_date": "2020-01", "end_date": "2023-01",
                    "is_current": False}])
    w = verified([pub(f"P{i}", "published", "matched", citations=10)
                  for i in range(5)])

    result = scoring.score_candidate(v, w, as_of=AS_OF)

    assert criterion(result, "publications")["points"] == 8.75
    assert criterion(result, "teaching_experience")["points"] == 12.00
    assert criterion(result, "doctorate")["points"] == 10.50
    assert criterion(result, "postdoctoral")["points"] == 0.00
    assert criterion(result, "masters")["points"] == 0.00
    assert criterion(result, "bachelors")["points"] == 0.00
    assert criterion(result, "industry_experience")["points"] == 0.00
    assert criterion(result, "research_visibility")["points"] == 2.50
    assert result["total_score"] == 33.75


def test_criteria_are_capped_at_their_weight():
    """A prolific candidate cannot exceed the weight."""
    w = verified([pub(f"P{i}", "published", "matched", citations=500)
                  for i in range(40)])
    result = scoring.score_candidate(validated(), w, as_of=AS_OF)
    assert criterion(result, "publications")["points"] == 35.0
    assert criterion(result, "research_visibility")["points"] == 5.0
    assert result["total_score"] <= result["max_score"]


# ---------------------------------------------------------------------------
# Missing data scores zero AND says so
# ---------------------------------------------------------------------------

def test_empty_profile_scores_zero_with_reasons():
    result = scoring.score_candidate(validated(), verified(), as_of=AS_OF)
    assert result["total_score"] == 0.0
    for c in result["criteria"]:
        assert c["insufficient_evidence"] is True
        assert "insufficient evidence" in c["note"]


def test_insufficient_evidence_is_distinguishable_from_a_poor_score():
    """
    A zero from ABSENT evidence and a zero from weak evidence must be
    tellable apart - they call for different decisions.
    """
    absent = scoring.score_candidate(validated(), verified(), as_of=AS_OF)
    # Present evidence, but nothing creditable: an in-progress paper.
    weak = scoring.score_candidate(
        validated(), verified([pub("X", "under_revision", "not_found")]),
        as_of=AS_OF)

    assert criterion(absent, "publications")["points"] == 0.0
    assert criterion(weak, "publications")["points"] == 0.0
    assert criterion(absent, "publications")["insufficient_evidence"] is True
    assert criterion(weak, "publications")["insufficient_evidence"] is False


# ---------------------------------------------------------------------------
# Durations: merged, not summed
# ---------------------------------------------------------------------------

def test_concurrent_roles_are_merged_not_summed():
    positions = [
        {"title": "Lecturer", "category": "teaching",
         "start_date": "2020-01", "end_date": "2022-01", "is_current": False},
        {"title": "Visiting Faculty", "category": "teaching",
         "start_date": "2020-01", "end_date": "2022-01", "is_current": False},
    ]
    years, _, _ = scoring.duration_years(positions, {"teaching"}, AS_OF)
    assert years == 2.0, "summing would wrongly give 4.0"


def test_ongoing_role_measured_against_recorded_as_of_date():
    positions = [{"title": "Assistant Professor", "category": "teaching",
                  "start_date": "2025-10", "end_date": None, "is_current": True}]
    years, _, _ = scoring.duration_years(positions, {"teaching"}, date(2026, 10, 15))
    assert years == 1.0
    # A different as_of gives a different answer - and it is RECORDED.
    years2, _, _ = scoring.duration_years(positions, {"teaching"}, date(2027, 10, 15))
    assert years2 == 2.0
    result = scoring.score_candidate(validated(positions=positions), verified(),
                                     as_of=AS_OF)
    assert result["as_of_date"] == "2026-10-15"


def test_unreadable_dates_are_reported_not_guessed():
    positions = [{"title": "Lecturer", "category": "teaching",
                  "start_date": "sometime", "end_date": None, "is_current": False}]
    years, evidence, problems = scoring.duration_years(positions, {"teaching"}, AS_OF)
    assert years == 0.0
    assert evidence == []
    assert problems and "unreadable" in problems[0]


# ---------------------------------------------------------------------------
# Publication credit, exclusions, and the visibility rule
# ---------------------------------------------------------------------------

def test_credit_exclusions_override_the_matrix():
    """A dissertation earns nothing even when OpenAlex matched it."""
    w = verified([pub("Thesis", "unknown", "matched",
                      exclusions=["thesis_or_dissertation"])])
    result = scoring.score_candidate(validated(), w, as_of=AS_OF)
    row = criterion(result, "publications")["evidence"][0]
    assert row["credit"] == 0.0
    assert "excluded" in row["reason"]
    assert schema.CREDIT_MATRIX[("unknown", "matched")] == 1.00  # would have been full


def test_untagged_unverifiable_entry_scores_zero_but_stays_visible():
    """
    DESIGN_DECISIONS.md section 2: no credit, no partial tier - but the
    entry remains in the evidence table with neutral wording.
    """
    w = verified([pub("Regional conference paper", "unknown", "not_found")])
    result = scoring.score_candidate(validated(), w, as_of=AS_OF)
    pubs = criterion(result, "publications")

    assert pubs["points"] == 0.0
    assert len(pubs["evidence"]) == 1                      # still shown
    row = pubs["evidence"][0]
    assert row["credit"] == 0.0
    assert row["note"] == "Unlabeled claim, not independently verifiable."
    # Wording is about our knowledge, not the candidate's honesty.
    assert "false" not in row["note"].lower()
    assert "fabricat" not in row["note"].lower()


def test_in_progress_work_never_earns_credit():
    w = verified([pub("Under revision", "under_revision", "matched")])
    result = scoring.score_candidate(validated(), w, as_of=AS_OF)
    assert criterion(result, "publications")["points"] == 0.0


def test_biblio_match_credits_like_a_title_match():
    title_match = verified([pub("A", "published", "matched")])
    biblio_match = verified([pub("A", "published", "matched_by_biblio")])
    a = scoring.score_candidate(validated(), title_match, as_of=AS_OF)
    b = scoring.score_candidate(validated(), biblio_match, as_of=AS_OF)
    assert criterion(a, "publications")["points"] == criterion(b, "publications")["points"]


def test_lookup_failure_is_flagged_not_silently_zeroed():
    """Assert the BEHAVIOUR, not the wording - the message has since been
    strengthened and a brittle string match would fail on an improvement."""
    w = verified([pub("A", "published", "lookup_failed")])
    result = scoring.score_candidate(validated(), w, as_of=AS_OF)
    pubs = criterion(result, "publications")
    assert pubs["indeterminate"] is True
    assert pubs["indeterminate_entries"] == 1
    assert result["scoring_reliable"] is False
    assert "not reliable" in pubs["note"]


def test_citations_counted_only_on_corroborated_work():
    w = verified([
        pub("Corroborated", "published", "matched", citations=30),
        pub("Not corroborated", "published", "not_found", citations=999),
    ])
    result = scoring.score_candidate(validated(), w, as_of=AS_OF)
    assert criterion(result, "research_visibility")["measured"] == 30


# ---------------------------------------------------------------------------
# The gate: separate, and never touching the score
# ---------------------------------------------------------------------------

def test_gate_met_by_doctorate():
    v = validated({"phd_status": wrap("awarded"), "phd_year": wrap(2021)})
    result = scoring.score_candidate(v, verified(), as_of=AS_OF)
    assert result["eligibility"]["outcome"] == "met"
    assert result["eligibility"]["affects_score"] is False


def test_gate_met_by_net_without_a_doctorate():
    v = validated({"phd_status": wrap("none"), "net_qualified": wrap(True),
                   "highest_degree": wrap("masters")})
    result = scoring.score_candidate(v, verified(), as_of=AS_OF)
    assert result["eligibility"]["outcome"] == "met"
    assert "NET/SET" in result["eligibility"]["reason"]


def test_gate_indeterminate_when_award_withheld():
    """A withheld claim routes to review, NOT to not_met."""
    v = validated({"highest_degree": wrap("phd"),
                   "phd_status": {"value": None, "unverified": True,
                                  "rejected_value": "awarded", "evidence": ""}},
                  summary={"doctorate_cross_check_failed": True})
    result = scoring.score_candidate(v, verified(), as_of=AS_OF)
    assert result["eligibility"]["outcome"] == "indeterminate"


def test_gate_not_met_with_no_doctorate_and_no_net():
    v = validated({"phd_status": wrap("none"), "highest_degree": wrap("masters")})
    result = scoring.score_candidate(v, verified(), as_of=AS_OF)
    assert result["eligibility"]["outcome"] == "not_met"


def test_gate_does_not_change_any_score():
    """Identical evidence must score identically whatever the gate says."""
    base_positions = [{"title": "Lecturer", "category": "teaching",
                       "start_date": "2020-01", "end_date": "2023-01",
                       "is_current": False}]
    pubs = verified([pub("A", "published", "matched")])

    eligible = scoring.score_candidate(
        validated({"phd_status": wrap("none"), "net_qualified": wrap(True)},
                  base_positions), pubs, as_of=AS_OF)
    gated = scoring.score_candidate(
        validated({"phd_status": wrap("none")}, base_positions), pubs, as_of=AS_OF)

    assert eligible["eligibility"]["outcome"] == "met"
    assert gated["eligibility"]["outcome"] == "not_met"
    for name in ("publications", "teaching_experience", "industry_experience"):
        assert criterion(eligible, name)["points"] == criterion(gated, name)["points"]


# ---------------------------------------------------------------------------
# Reviewer flags: advisory only
# ---------------------------------------------------------------------------

def test_assistantship_during_own_degree_is_not_flagged():
    """The ordinary structure of doctoral study must not raise an alarm."""
    v = validated({"phd_start_year": wrap(2020), "phd_year": wrap(None)},
                  [{"title": "Teaching Assistant", "organisation": "Same Uni",
                    "category": "teaching", "start_date": "2020-09",
                    "end_date": "2023-05", "is_current": False}])
    result = scoring.score_candidate(v, verified(), as_of=AS_OF)
    assert result["reviewer_flags"] == []


def test_full_time_job_during_degree_is_flagged_neutrally():
    v = validated({"phd_start_year": wrap(2025), "phd_year": wrap(None)},
                  [{"title": "Assistant Professor", "organisation": "Other Uni",
                    "category": "teaching", "start_date": "2025-07",
                    "end_date": None, "is_current": True}])
    result = scoring.score_candidate(v, verified(), as_of=AS_OF)
    assert len(result["reviewer_flags"]) == 1
    flag = result["reviewer_flags"][0]
    assert flag["affects_score"] is False and flag["affects_gate"] is False
    assert "please check" in flag["message"]
    for accusatory in ("discrepan", "inconsist", "false", "suspicious"):
        assert accusatory not in flag["message"].lower()


def test_flags_do_not_change_the_total():
    positions = [{"title": "Assistant Professor", "organisation": "Other Uni",
                  "category": "teaching", "start_date": "2025-07",
                  "end_date": None, "is_current": True}]
    flagged = scoring.score_candidate(
        validated({"phd_start_year": wrap(2025)}, positions), verified(), as_of=AS_OF)
    unflagged = scoring.score_candidate(
        validated({}, positions), verified(), as_of=AS_OF)
    assert flagged["reviewer_flags"] and not unflagged["reviewer_flags"]
    assert (criterion(flagged, "teaching_experience")["points"]
            == criterion(unflagged, "teaching_experience")["points"])


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def test_every_criterion_reports_its_source_fields():
    result = scoring.score_candidate(
        validated({"phd_status": wrap("awarded"), "phd_year": wrap(2020)}),
        verified([pub("A", "published", "matched")]), as_of=AS_OF)
    for c in result["criteria"]:
        assert c["source_fields"], f"{c['criterion']} reports no source field"


def test_output_is_labelled_a_demo_rubric():
    result = scoring.score_candidate(validated(), verified(), as_of=AS_OF)
    assert "NOT LNMIIT" in result["rubric"]["disclaimer"]
    assert result["human_decision_required"] is True


def test_in_progress_doctorate_earns_no_credit():
    """
    User decision 2026-10-15: flat 0.5 replaced with 0.00.

    A part-mark for an unfinished doctorate is a guess about how far along
    it is, and a first-year student is not a thesis-submitted one. Whether
    an in-progress PhD makes someone considerable at all is the gate's
    question, answered separately.
    """
    v = validated({"phd_status": wrap("pursuing"),
                   "highest_degree": wrap("phd_pursuing"),
                   "phd_start_year": wrap(2025)})
    result = scoring.score_candidate(v, verified(), as_of=AS_OF)
    doctorate = criterion(result, "doctorate")

    assert doctorate["points"] == 0.0
    assert "no credit until the award is evidenced" in doctorate["note"]
    # Not "insufficient evidence": we know exactly what the position is.
    assert doctorate["insufficient_evidence"] is False
    # The gate still treats them as considerable-pending-review.
    assert result["eligibility"]["outcome"] == "indeterminate"


def test_awarded_doctorate_at_a_listed_institution_earns_full_credit():
    v = validated({"phd_status": wrap("awarded"), "phd_year": wrap(2021),
                   "phd_institution": wrap("IIT Bombay")})
    result = scoring.score_candidate(v, verified(), as_of=AS_OF)
    doctorate = criterion(result, "doctorate")
    assert doctorate["points"] == scoring.WEIGHTS["doctorate"]
    assert doctorate["needs_review"] is False


def test_awarded_doctorate_with_no_institution_named_is_flagged_not_failed():
    """
    The tier multiplier scales a doctorate; it must never decide whether
    one exists. An unnamed institution costs the tier difference and
    raises a review flag - it does not zero the criterion.
    """
    v = validated({"phd_status": wrap("awarded"), "phd_year": wrap(2021)})
    doctorate = criterion(
        scoring.score_candidate(v, verified(), as_of=AS_OF), "doctorate")
    assert doctorate["points"] == pytest.approx(15 * 0.7)
    assert doctorate["points"] > 0
    assert doctorate["needs_review"] is True
    assert doctorate["insufficient_evidence"] is False


# ---------------------------------------------------------------------------
# Teaching assistantship weighting (user decision, 2026-10-15)
# ---------------------------------------------------------------------------

def test_assistantship_months_count_at_half():
    """2 years of TA work = 1.0 effective year, so half the points."""
    ta = [{"title": "Teaching Assistant", "category": "teaching",
           "start_date": "2020-01", "end_date": "2022-01", "is_current": False}]
    faculty = [{"title": "Lecturer", "category": "teaching",
                "start_date": "2020-01", "end_date": "2022-01", "is_current": False}]

    ta_years, _, _, _ = scoring.teaching_years_weighted(ta, AS_OF)
    faculty_years, _, _, _ = scoring.teaching_years_weighted(faculty, AS_OF)

    assert faculty_years == 2.0
    assert ta_years == 1.0
    assert ta_years == faculty_years * scoring.ASSISTANTSHIP_CREDIT


def test_course_titles_classified_by_their_section():
    """"PH-204: Physics Lab IV" is only identifiable via its heading."""
    positions = [{"title": "PH-204: Physics Lab IV",
                  "source_section": "Teaching Assistantship",
                  "category": "teaching", "start_date": "2016-07",
                  "end_date": "2017-06", "is_current": False}]
    years, evidence, _, uncertain = scoring.teaching_years_weighted(positions, AS_OF)
    assert evidence[0]["kind"] == "assistantship"
    assert years == 0.46          # 11 months x 0.5 / 12
    assert uncertain == []


def test_concurrent_appointment_and_assistantship_counted_once():
    """
    The same month cannot be both. The appointment wins - the two must not
    stack past wall-clock time.
    """
    positions = [
        {"title": "Assistant Professor", "category": "teaching",
         "start_date": "2020-01", "end_date": "2022-01", "is_current": False},
        {"title": "Teaching Assistant", "category": "teaching",
         "start_date": "2020-01", "end_date": "2022-01", "is_current": False},
    ]
    years, _, _, _ = scoring.teaching_years_weighted(positions, AS_OF)
    assert years == 2.0, "overlap must not add 0.5x on top of the appointment"


def test_unclassifiable_role_is_flagged_not_silently_defaulted():
    """Counted at the full rate, but the uncertainty is stated."""
    positions = [{"title": "Resource Person", "category": "teaching",
                  "start_date": "2022-01", "end_date": "2024-01",
                  "is_current": False}]
    years, evidence, _, uncertain = scoring.teaching_years_weighted(positions, AS_OF)

    assert years == 2.0                      # full rate, not downgraded
    assert len(uncertain) == 1
    assert "please check" in uncertain[0]

    result = scoring.score_candidate(validated(positions=positions), verified(),
                                     as_of=AS_OF)
    teaching = criterion(result, "teaching_experience")
    assert teaching["unclassified_roles"]
    assert "UNCLASSIFIED" in teaching["note"]


def test_assistant_professor_is_not_read_as_an_assistantship():
    """One word apart; getting this wrong would halve real faculty time."""
    assert scoring.classify_teaching_role(
        {"title": "Assistant Professor (CSE)", "source_section": "Experience"}
    ) == "appointment"


def test_publication_threshold_is_twenty():
    """Raised from 10 so the criterion still separates strong candidates."""
    assert scoring.FULL_MARKS_PUBLICATION_CREDIT == 20.0
    ten = verified([pub(f"P{i}", "published", "matched") for i in range(10)])
    nineteen = verified([pub(f"P{i}", "published", "matched") for i in range(19)])
    a = scoring.score_candidate(validated(), ten, as_of=AS_OF)
    b = scoring.score_candidate(validated(), nineteen, as_of=AS_OF)
    # 10/20 = 0.50 -> 17.50 ; 19/20 = 0.95 -> 33.25, on the 35-mark weight
    assert criterion(a, "publications")["points"] == 17.5
    assert criterion(b, "publications")["points"] == 33.25
    assert criterion(a, "publications")["points"] != criterion(b, "publications")["points"]


# ---------------------------------------------------------------------------
# A failed lookup must not be presentable as a judgement on the candidate
# ---------------------------------------------------------------------------

def test_total_lookup_outage_marks_the_score_unreliable():
    """
    With the index unreachable, every entry is INDETERMINATE. The
    arithmetic still needs a number, but the result must announce that it
    cannot be trusted - otherwise a service outage silently collapses
    every candidate's publication score and the ranking still looks normal.
    """
    pubs = verified([pub(f"P{i}", "published", "lookup_failed")
                     for i in range(19)])
    result = scoring.score_candidate(validated(), pubs, as_of=AS_OF)

    assert result["scoring_reliable"] is False
    assert result["unreliable_reasons"]
    assert "could not be verified" in result["unreliable_reasons"][0]

    criterion_row = criterion(result, "publications")
    assert criterion_row["indeterminate"] is True
    assert criterion_row["indeterminate_entries"] == 19
    assert "not reliable" in criterion_row["note"]
    # And it is NOT dressed up as an evidence problem on the candidate's side.
    assert criterion_row["insufficient_evidence"] is False


def test_healthy_run_is_marked_reliable():
    pubs = verified([pub("P1", "published", "matched")])
    result = scoring.score_candidate(validated(), pubs, as_of=AS_OF)
    assert result["scoring_reliable"] is True
    assert result["unreliable_reasons"] == []
    assert criterion(result, "publications")["indeterminate"] is False


def test_a_genuine_zero_is_not_marked_unreliable():
    """Distinguish 'checked, found nothing' from 'could not check'."""
    pubs = verified([pub("P1", "unknown", "not_found")])
    result = scoring.score_candidate(validated(), pubs, as_of=AS_OF)
    assert criterion(result, "publications")["points"] == 0.0
    assert result["scoring_reliable"] is True        # the lookup DID happen
    assert criterion(result, "publications")["indeterminate"] is False


# ---------------------------------------------------------------------------
# Candidate_06 regressions: a documented role must never score zero
# ---------------------------------------------------------------------------

def test_day_precision_dates_are_parsed():
    """
    "Oct 4, 2024" is how Candidate_06's CV writes the start of a currently
    held Assistant Professor post. The parser rejected it, the position
    was discarded as unusable, and teaching scored 0 with "no teaching
    position with a readable date range".
    """
    for text, expected in [
        ("Oct 4, 2024", (2024, 10)),
        ("October 4, 2024", (2024, 10)),
        ("4 Oct 2024", (2024, 10)),
        ("4th October 2024", (2024, 10)),
        ("2024-10-04", (2024, 10)),
        ("2025/08", (2025, 8)),
    ]:
        assert scoring.parse_date(text) == expected, text


def test_ambiguous_numeric_date_is_still_refused():
    """04/10/2024 could be April or October. Refuse rather than guess."""
    assert scoring.parse_date("04/10/2024") is None
    assert scoring.parse_date("sometime") is None
    assert scoring.parse_date("2024-13") is None


def test_currently_held_professorship_scores_teaching_points():
    """End to end for the reported bug, with hand-checkable arithmetic."""
    positions = [{
        "title": "Assistant Professor",
        "organisation": "Lovely Professional University",
        "category": "teaching",
        "start_date": "Oct 4, 2024",
        "end_date": None,
        "is_current": True,
        "evidence": "Lovely Professional University  Oct 4, 2024 - Present",
    }]
    # 2024-10 to 2026-10 is 24 months of appointment time = 2.0 years.
    years, evidence, problems, _ = scoring.teaching_years_weighted(
        positions, date(2026, 10, 20))
    assert problems == []
    assert years == 2.0
    assert evidence[0]["kind"] == "appointment"

    result = scoring.score_candidate(
        validated(positions=positions), verified(), as_of=date(2026, 10, 20))
    teaching = criterion(result, "teaching_experience")
    # 2.0 / 5.0 saturation = 0.4 -> 0.4 x 20 = 8.00 (Assistant Professor
    # is the base rank, so no rank multiplier applies)
    assert teaching["points"] == 8.0
    assert teaching["insufficient_evidence"] is False


def test_short_internship_scores_industry_points():
    """Kalvium: July 2024 - Aug 2024 is one month, and one month is not zero."""
    positions = [{
        "title": "Tech Mentor Intern", "organisation": "Kalvium",
        "category": "industry", "start_date": "July 2024",
        "end_date": "Aug. 2024", "is_current": False, "evidence": "Kalvium",
    }]
    result = scoring.score_candidate(
        validated(positions=positions), verified(), as_of=date(2026, 10, 20))
    industry = criterion(result, "industry_experience")
    assert industry["measured"] == 0.08          # 1 month
    assert industry["points"] > 0
    assert industry["insufficient_evidence"] is False
