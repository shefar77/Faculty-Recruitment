"""
The candidate profile summary.

All fixtures are SYNTHETIC. The summary is the output most likely to be
read quickly and trusted, so the tests here are mostly about what it must
NOT say: no invented qualification, no judgement, no completed degree
where there is an ongoing one, and no fragment of a value it could not
render.

Run:  python -m pytest tests/test_summary.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import degrees  # noqa: E402
import summary  # noqa: E402


def wrap(value, evidence="evidence text"):
    return {"value": value, "evidence": evidence}


def audit(profile=None, criteria=None, reliable=True):
    return {
        "validation": {"profile": dict(profile or {})},
        "scoring": {"criteria": list(criteria or []),
                    "scoring_reliable": reliable},
    }


def criterion(name, measured=0, evidence=None, **extra):
    return {"criterion": name, "measured": measured,
            "evidence": list(evidence or []), **extra}


def text_of(*args, **kwargs):
    return summary.build_summary(audit(*args, **kwargs))["text"]


# ---------------------------------------------------------------------------
# Completed versus ongoing - the distinction that matters most
# ---------------------------------------------------------------------------

def test_an_awarded_doctorate_is_stated_as_held():
    prose = text_of({"phd_status": wrap("awarded"), "phd_year": wrap(2020),
                     "phd_subject": wrap("Computer Science"),
                     "phd_institution": wrap("IIT Delhi")})
    assert "Holds a PhD in Computer Science from IIT Delhi, awarded 2020." \
        in prose


def test_a_submitted_thesis_is_never_described_as_a_held_doctorate():
    prose = text_of({"phd_status": wrap("thesis_submitted"),
                     "phd_institution": wrap("IIT Delhi"),
                     "masters_degree": wrap("M.Tech"),
                     "masters_subject": wrap("Computer Science")})
    assert "Holds a PhD" not in prose
    assert "submitted a doctoral thesis" in prose
    assert "awaiting the award" in prose


def test_an_ongoing_doctorate_leads_with_the_completed_qualification():
    """
    A reviewer skims. "Highest completed qualification is X" first means
    the sentence cannot be half-read as a doctorate the candidate holds.
    """
    prose = text_of({"phd_status": wrap("pursuing"),
                     "phd_institution": wrap("IIT Delhi"),
                     "masters_degree": wrap("M.Tech"),
                     "masters_subject": wrap("Computer Science")})
    assert prose.startswith("Highest completed qualification is M.Tech")
    assert "is enrolled for a doctorate" in prose


def test_a_candidate_with_only_a_bachelors_is_described_accurately():
    prose = text_of({"bachelors_degree": wrap("B.Sc"),
                     "bachelors_subject": wrap("Physics"),
                     "bachelors_year": wrap(2012)})
    assert "B.Sc in Physics" in prose
    assert "PhD" not in prose and "Master" not in prose


# ---------------------------------------------------------------------------
# It may not invent, and it may not judge
# ---------------------------------------------------------------------------

def test_nothing_is_said_when_the_cv_yields_nothing():
    result = summary.build_summary(audit())
    assert "did not provide enough information" in result["text"]
    assert result["sentences"] == []
    assert result["unavailable"]


def test_no_evaluative_language_anywhere():
    prose = text_of(
        {"phd_status": wrap("awarded"), "phd_year": wrap(2015),
         "phd_institution": wrap("IIT Bombay"),
         "research_area": wrap("computer vision")},
        [criterion("teaching_experience", 12.0),
         criterion("publications", evidence=[
             {"title": "P", "credit": 1.0, "verification": "matched"}])])
    for banned in ("excellent", "strong", "outstanding", "well suited",
                   "highly", "impressive", "ideal", "promising", "weak",
                   "poor"):
        assert banned not in prose.lower(), banned


def test_a_missing_qualification_is_recorded_as_unavailable_not_guessed():
    result = summary.build_summary(audit(
        {"phd_status": wrap("pursuing"), "phd_institution": wrap("IIT Delhi")}))
    assert result["unavailable"] == ["no completed degree is recorded"]
    assert "Master" not in result["text"]


def test_the_summary_is_capped_at_four_sentences():
    result = summary.build_summary(audit(
        {"phd_status": wrap("awarded"), "phd_year": wrap(2015),
         "phd_institution": wrap("IIT Bombay"),
         "research_area": wrap("computer vision"),
         "net_qualified": wrap(True)},
        [criterion("teaching_experience", 12.0),
         criterion("industry_experience", 3.0),
         criterion("postdoctoral", 2.0),
         criterion("research_visibility", 40),
         criterion("publications", evidence=[
             {"title": "P", "credit": 1.0, "verification": "matched"}])]))
    assert 1 <= len(result["sentences"]) <= summary.MAX_SENTENCES


def test_no_model_is_called():
    assert summary.build_summary(audit())["generated_by"] == "deterministic"


# ---------------------------------------------------------------------------
# Defects found while building it
# ---------------------------------------------------------------------------

def test_the_most_recent_role_is_chosen_by_date_not_by_string_order():
    """
    Sorting the raw date strings put "Nov 2025" above "2021", because
    'N' sorts after '2'. The summary then named the wrong post as the
    most recent one - a wrong fact stated confidently.
    """
    prose = text_of({}, [criterion("teaching_experience", 8.0, evidence=[
        {"title": "Assistant Professor", "organisation": "A College",
         "from": "Nov 2015", "months": 60},
        {"title": "Associate Professor", "organisation": "B College",
         "from": "2021-01", "months": 36},
    ])])
    assert "most recently as Associate Professor at B College" in prose


def test_an_unusable_organisation_is_omitted_rather_than_truncated():
    """
    CVs put course titles and postal addresses in the employer field.
    A mangled fragment reads as a bug; omitting it does not, and the
    full value is still in the evidence table.
    """
    prose = text_of({}, [criterion("teaching_experience", 2.0, evidence=[
        {"title": "Teaching Associate", "from": "2025-11", "months": 6,
         "organisation": "Applied Artificial Intelligence for All: "
                         "Foundations to Job-Readiness Remote Programme"},
    ])])
    assert "most recently as Teaching Associate." in prose
    assert "…" not in prose


def test_no_placeholder_ever_reaches_the_reader():
    """Guards the 'at None' leak: an omitted value must omit its clause."""
    for profile, criteria in (
        ({"phd_status": wrap("awarded"), "phd_year": wrap(2020)}, []),
        ({"phd_status": wrap("thesis_submitted")}, []),
        ({"masters_degree": wrap("M.Tech")}, []),
        ({}, [criterion("teaching_experience", 3.0,
                        evidence=[{"title": "Lecturer", "from": "2020-01"}])]),
    ):
        prose = text_of(profile, criteria)
        assert "None" not in prose
        assert "  " not in prose, "a dropped clause left a double space"


def test_a_raw_extractor_spelling_is_tidied_for_the_reader():
    """The extractor returns "mtech"; a professor should not see that."""
    assert degrees.display_name("mtech") == "M.Tech"
    assert degrees.display_name("B. Tech.") == "B.Tech"
    assert degrees.display_name("Master of Technology") == \
        "Master of Technology"
    assert "mtech" not in text_of({"masters_degree": wrap("mtech")})


def test_durations_are_written_consistently_as_words():
    """'ten years and 10 months' mixed a word with a digit."""
    prose = text_of({}, [criterion("teaching_experience", 10.83)])
    assert "ten years and ten months" in prose


# ---------------------------------------------------------------------------
# It reports what the evidence supports, and only that
# ---------------------------------------------------------------------------

def test_publication_counts_come_from_the_verification_result():
    prose = text_of({}, [criterion("publications", evidence=[
        {"title": "A", "credit": 1.0, "verification": "matched"},
        {"title": "B", "credit": 0.25, "verification": "not_found"},
        {"title": "C", "credit": 0.0, "verification": "not_found"},
    ])])
    assert "3 publications listed, 1 of them corroborated" in prose


def test_granted_patents_are_distinguished_from_pending_ones():
    granted = text_of({}, [criterion("publications", evidence=[
        {"kind": "patent", "title": "P", "status": "granted", "credit": 0.75}])])
    pending = text_of({}, [criterion("publications", evidence=[
        {"kind": "patent", "title": "P", "status": "filed", "credit": 0.0}])])
    assert "1 granted patent" in granted
    assert "not yet granted" in pending


def test_an_unreliable_run_is_disclosed_in_the_summary():
    prose = summary.build_summary(audit(
        {"net_qualified": wrap(True)}, [], reliable=False))["text"]
    assert "could not be verified" in prose


def test_a_net_pass_is_mentioned_because_it_decides_eligibility():
    assert "NET/SET" in text_of({"net_qualified": wrap(True)})


def test_building_a_summary_is_cheap_enough_to_do_on_every_read():
    """
    The requirement was no extra model call per upload. This is the
    measurement behind that claim: it runs at read time instead, and
    costs microseconds rather than the minutes an Ollama call adds.
    """
    import time
    record = audit(
        {"phd_status": wrap("awarded"), "phd_year": wrap(2015),
         "phd_institution": wrap("IIT Bombay"),
         "research_area": wrap("computer vision")},
        [criterion("teaching_experience", 12.0, evidence=[
            {"title": "Professor", "organisation": "X", "from": "2013-01"}]),
         criterion("publications", evidence=[
             {"title": f"P{i}", "credit": 1.0, "verification": "matched"}
             for i in range(20)])])
    start = time.perf_counter()
    for _ in range(200):
        summary.build_summary(record)
    per_call_ms = (time.perf_counter() - start) * 1000 / 200
    assert per_call_ms < 5, f"{per_call_ms:.3f} ms per call is too slow"
