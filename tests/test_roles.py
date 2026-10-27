"""
GENERALISATION TESTS for the role taxonomy.

Every fixture here is SYNTHETIC. No candidate id, no real person, no
employer taken from the sample CVs. The point of the suite is to prove the
classifier works on vocabulary it was not written against, so using the
demo CVs' wording would defeat it.

The titles are drawn from how academic posts are actually advertised
across UK, US, Indian and EU institutions, because a CV screener that
only understands one country's titles is not generalised.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import roles  # noqa: E402


def position(title, organisation=None, section=None, **extra):
    return {"title": title, "organisation": organisation,
            "source_section": section, **extra}


# ---------------------------------------------------------------------------
# Faculty appointments, across naming conventions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Assistant Professor",
    "Associate Professor",
    "Professor",
    "Asst. Prof.",
    "Lecturer",
    "Senior Lecturer",
    "Reader",
    "Visiting Faculty",
    "Adjunct Professor",
    "Adjunct Lecturer",
    "Clinical Assistant Professor",
    "Teaching Fellow",
    "Instructor",
    "Course Coordinator",
    "Module Leader",
    "Head of Department",
    "Preceptor",
    "Tenure-Track Assistant Professor",
])
def test_faculty_titles_are_appointments(title):
    kind, reason = roles.teaching_kind(position(title))
    assert kind == "appointment", f"{title!r} -> {kind} ({reason})"
    assert reason


@pytest.mark.parametrize("title", [
    "Teaching Assistant",
    "Graduate Teaching Assistant",
    "Lab Instructor",
    "Laboratory Demonstrator",
    "Demonstrator",
    "Tutor",
    "Tutorial Assistant",
    "Grader",
    "Peer Tutor",
    "Student Assistant",
])
def test_assistantship_titles_are_assistantships(title):
    kind, reason = roles.teaching_kind(position(title))
    assert kind == "assistantship", f"{title!r} -> {kind} ({reason})"
    assert reason


@pytest.mark.parametrize("title", [
    "Teaching Assistant Professor",     # a real US teaching-track post
    "Research Assistant Professor",
])
def test_the_head_noun_wins_over_an_assistantship_modifier(title):
    """
    These contain "Assistant" but are full faculty appointments. A keyword
    search cannot tell them from a teaching assistantship; the head-noun
    rule can.
    """
    kind, reason = roles.teaching_kind(position(title))
    assert kind == "appointment", f"{title!r} -> {kind} ({reason})"


@pytest.mark.parametrize("title", [
    "Teaching Assistant / Lab Instructor",
    "Lab Instructor and Teaching Assistant",
    "Demonstrator / Lab Instructor",
])
def test_a_combined_assistantship_title_is_not_read_as_a_professorship(title):
    """
    Regression. A combined title holds several assistantship phrases, and
    the faculty word ("Instructor") sits inside the SECOND one. Checking
    only the first match read these as faculty appointments and awarded
    doctoral students full professorial teaching credit.
    """
    kind, reason = roles.teaching_kind(position(title))
    assert kind == "assistantship", f"{title!r} -> {kind} ({reason})"


def test_a_course_code_under_an_assistantship_heading_is_an_assistantship():
    """
    Some CVs list taught courses by code under one heading. The title
    matches no role vocabulary at all, so the section is the only signal.
    """
    kind, reason = roles.teaching_kind(
        position("CHEM-210: Organic Chemistry Lab",
                 section="Teaching Assistantship"))
    assert kind == "assistantship"
    assert "heading" in reason


def test_an_unrecognisable_title_is_uncertain_not_guessed():
    kind, reason = roles.teaching_kind(position("Resource Person"))
    assert kind == "uncertain"
    assert reason


# ---------------------------------------------------------------------------
# Employers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Imperial College London",
    "Technical University of Munich",
    "Indian Institute of Technology Bombay",
    "National Institute of Technology Trichy",
    "Faculty of Engineering, Anna University",
    "Department of Physics, Durham University",
    "National Physical Laboratory",
    "Max Planck Institute for Informatics",
    "Tata Institute of Fundamental Research",
    "Government Polytechnic",
])
def test_academic_employers_are_recognised(name):
    kind, reason = roles.employer_kind(name)
    assert kind == "academic", f"{name!r} -> {kind} ({reason})"


@pytest.mark.parametrize("name", [
    "Infosys Technologies Ltd",
    "Nimbus Software Solutions Pvt. Ltd.",
    "Helios Systems Inc.",
    "Orion Consultancy LLP",
])
def test_commercial_employers_are_recognised(name):
    kind, reason = roles.employer_kind(name)
    assert kind == "commercial", f"{name!r} -> {kind} ({reason})"


@pytest.mark.parametrize("name", ["Veltrix", "KODEX", "Zephyr Group"])
def test_an_unmarked_employer_name_is_unknown_not_commercial(name):
    """
    The distinction that matters: a name with no academic marker is NOT
    thereby a company. Government research bodies are routinely known only
    by an acronym, and treating "no university word" as "industry" filed
    one as industry in production.
    """
    kind, _ = roles.employer_kind(name)
    assert kind == "unknown"


def test_a_missing_employer_is_unknown():
    assert roles.employer_kind(None)[0] == "unknown"
    assert roles.employer_kind("")[0] == "unknown"


# ---------------------------------------------------------------------------
# Whole-position classification and its precedence rules
# ---------------------------------------------------------------------------

def test_a_teaching_title_beats_a_commercial_employer():
    verdict = roles.classify_position(
        position("Senior Lecturer", "Helios Systems Inc."))
    assert verdict["category"] == "teaching"


def test_a_research_title_beats_an_unknown_employer():
    verdict = roles.classify_position(
        position("Postdoctoral Researcher", "Veltrix"))
    assert verdict["category"] == "research"
    assert verdict["needs_review"] is False


def test_an_uninformative_title_at_a_company_is_industry():
    verdict = roles.classify_position(
        position("Associate Consultant", "Orion Consultancy LLP"))
    assert verdict["category"] == "industry"


def test_an_uninformative_title_at_an_unknown_employer_is_flagged():
    verdict = roles.classify_position(position("Summer Intern", "Veltrix"))
    assert verdict["category"] == "other"
    assert verdict["needs_review"] is True
    assert verdict["confidence"] == "low"


def test_an_uninformative_title_at_a_university_is_flagged_not_assumed():
    """
    Working at a university does not make a post academic - universities
    employ administrators, technicians and caterers. Without a role the
    CV names, this must go to a human.
    """
    verdict = roles.classify_position(
        position("Project Officer", "Imperial College London"))
    assert verdict["category"] == "other"
    assert verdict["needs_review"] is True


@pytest.mark.parametrize("title", [
    "Workshop on Machine Learning",
    "Faculty Development Programme",
    "Summer School on Robotics",
    "AWS Certification",
])
def test_training_entries_are_not_employment(title):
    verdict = roles.classify_position(position(title, "Imperial College London"))
    assert verdict["category"] == "other"
    assert verdict["needs_review"] is False      # not ambiguous - just not a job
    assert "training" in verdict["reason"] or "course" in verdict["reason"]


def test_every_verdict_carries_a_reason():
    """A classification a reviewer cannot interrogate is not evidence."""
    for title in ["Assistant Professor", "Teaching Assistant", "Veltrix role",
                  "Research Fellow", "Workshop on AI"]:
        verdict = roles.classify_position(position(title, "Veltrix"))
        assert verdict["reason"], f"{title!r} produced no reason"
        assert verdict["confidence"] in ("high", "medium", "low")


# ---------------------------------------------------------------------------
# No production rule may name a candidate
# ---------------------------------------------------------------------------

def test_the_taxonomy_names_no_candidate_or_real_employer():
    """
    Guards the whole point of this module: production classification must
    not encode anything from the demo CVs.
    """
    source = (ROOT / "src" / "roles.py").read_text(encoding="utf-8").lower()
    for banned in ["candidate_0", "lovely professional", "kalvium",
                   "mangalmay", "galgotias", "nielit", "lueurtech",
                   "teligenz", "amar deep", "shiva dixit", "ayush kumar",
                   "deepali", "arpit"]:
        assert banned not in source, f"roles.py references {banned!r}"
