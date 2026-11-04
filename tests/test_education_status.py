"""
Degree completion: telling a finished degree from an unfinished one.

WHAT WENT WRONG, AND WHY IT IS THIS FILE'S SUBJECT
    cross_check_doctorate searched the WHOLE CV for words like "ongoing"
    and "pursuing" before deciding whether to trust an "awarded" claim.
    Candidate_08 supervises three doctoral students, so his CV says
    "Ongoing" three times under "Research Student Supervision (Ph.D.)".
    Those words - about his STUDENTS - withheld his OWN doctorate,
    awarded in 2022. He lost the 15 doctorate marks and was routed to
    human review with the reason "doctorate evidenced but its award is
    not confirmed".

    Nothing about that was specific to him. Any candidate senior enough
    to supervise a PhD, or whose CV mentioned an ongoing project, was
    exposed. The old suite passed throughout, because every fixture in
    it was a short CV with no second section to be confused by.

    So the tests below are mostly about SCOPE: which part of the document
    a question is allowed to read. The rule is that a statement about the
    candidate's degree has to come from the candidate's degree entry.

ALL FIXTURES ARE SYNTHETIC except the real-PDF regression at the end,
which is skipped when the file is absent.

Run:  python -m pytest tests/test_education_status.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import scoring    # noqa: E402
import validate   # noqa: E402


def wrap(value, evidence=""):
    return {"value": value, "evidence": evidence}


def run(cv_text: str, **profile) -> dict:
    record = {"candidate_id": "Synthetic", "profile": profile,
              "publications": []}
    return validate.validate_record(record, cv_text)


def withheld(result: dict) -> bool:
    return result["validation_summary"]["doctorate_cross_check_failed"]


def gate(result: dict) -> dict:
    return scoring.evaluate_gate(result["profile"],
                                 result["validation_summary"])


# ---------------------------------------------------------------------------
# The defect: somebody else's status read as the candidate's own
# ---------------------------------------------------------------------------

SUPERVISOR_CV = """Prabhat Dansena

Research Highlights
Ph.D. Supervision      1 Awarded, 3 Ongoing

Education

2015 - 2022    Ph.D. Computer Science and Engineering
               Indian Institute of Technology (Indian School of Mines) Dhanbad.
               Thesis title: Developing Ink Discrimination based Methods.

2013 - 2015    M.Tech. Information Technology
               University of Hyderabad, Telangana, India.

Research Student Supervision (Ph.D.)

Awarded: 2025  Title: Enhancing Camouflaged Object Detection
               Name: Dr. Isha Padhy
Ongoing        Title: Suspicious Behavior Detection in Video Surveillance
Ongoing        Title: Handwritten Document Image Processing
Ongoing        Title: Devising Strategist for Alteration Detection
"""

SUPERVISOR_PROFILE = dict(
    phd_status=wrap("awarded", "Ph.D. Computer Science and Engineering"),
    phd_start_year=wrap(2015, "2015 - 2022"),
    phd_year=wrap(2022, "2015 - 2022"),
    phd_institution=wrap(
        "Indian Institute of Technology (Indian School of Mines) Dhanbad.",
        "Indian Institute of Technology (Indian School of Mines) Dhanbad."),
)


def test_supervising_ongoing_students_does_not_withhold_your_own_doctorate():
    """The reported defect, reduced to its cause."""
    result = run(SUPERVISOR_CV, **SUPERVISOR_PROFILE)
    assert not withheld(result)
    assert result["profile"]["phd_status"]["value"] == "awarded"
    assert result["profile"]["phd_year"]["value"] == 2022


def test_the_supervisor_is_eligible_and_scores_the_doctorate_in_full():
    """
    The validation fix is only worth anything if it reaches the gate and
    the score. Both read the same validated profile, so this is the test
    that the whole chain agrees.
    """
    result = run(SUPERVISOR_CV, **SUPERVISOR_PROFILE)
    assert gate(result)["outcome"] == "met"

    criterion = scoring.score_doctorate(result["profile"], gate(result))
    # "In full" is a claim about the QUALIFICATION, which is what the
    # status fix governs: the award is recognised and credited at 1.0.
    # The awarding institution then scales it, and asserting the product
    # would silently pin this test to one institution's tier.
    qualification = next(f for f in criterion.factors
                         if f["factor"] == "qualification")
    assert qualification["value"] == 1.0
    institution = next(f for f in criterion.factors
                       if f["factor"] == "institution")
    assert criterion.points == pytest.approx(
        scoring.WEIGHTS["doctorate"] * institution["value"])
    assert criterion.insufficient_evidence is False


def test_an_ongoing_project_elsewhere_is_not_an_ongoing_degree():
    result = run("""Asha Menon

Education
Ph.D. in Physics, IISc Bangalore          Aug 2015 - May 2021

Projects
Ongoing: Quantum sensing testbed
Ongoing: Materials discovery pipeline
""",
                 phd_status=wrap("awarded", "Ph.D. in Physics, IISc Bangalore"),
                 phd_start_year=wrap(2015, "Aug 2015 - May 2021"),
                 phd_year=wrap(2021, "Aug 2015 - May 2021"))
    assert not withheld(result)


def test_an_employment_row_ending_till_date_is_not_an_ongoing_degree():
    """
    The entry ABOVE the degree must not leak into it. This is why the
    scope reads forward from the degree line and never backward.
    """
    result = run("""Ravi Shankar

Academic Appointments
2023 - Till Date   Assistant Professor, CGU Bhubaneswar

Education
Ph.D. Computer Science, IIT Dhanbad       2015 - 2022
""",
                 phd_status=wrap("awarded", "Ph.D. Computer Science, IIT Dhanbad"),
                 phd_start_year=wrap(2015, "2015 - 2022"),
                 phd_year=wrap(2022, "2015 - 2022"))
    assert not withheld(result)
    assert gate(result)["outcome"] == "met"


# ---------------------------------------------------------------------------
# The guard still has to do its job
# ---------------------------------------------------------------------------

def test_an_open_ended_doctorate_row_is_still_withheld():
    """
    The original defect this guard was built for: "2020 - Ph.D." with
    nothing after the dash, read by the model as awarded in 2020.
    """
    result = run("""Arpit Singh Yadav
Ph.D. Research Scholar, Department of Electronics

Academic Qualifications
2020   -  Ph.D.    Indian Institute of Information Technology   7.5 CGPA
""",
                 phd_status=wrap("awarded", "2020  -  Ph.D.  Indian Institute"),
                 phd_start_year=wrap(2020, "2020  -  Ph.D.  Indian Institute"),
                 phd_year=wrap(2020, "2020  -  Ph.D.  Indian Institute"))
    assert withheld(result)
    assert result["profile"]["phd_status"]["value"] is None
    assert gate(result)["outcome"] == "indeterminate"


def test_present_inside_the_degree_entry_is_withheld():
    result = run("""Neha Gupta

Education
Ph.D. in Computer Science, IIT Bombay      2019 - Present
M.Tech, NIT Trichy                         2015 - 2017
""",
                 phd_status=wrap("awarded", "Ph.D. in Computer Science, IIT Bombay"),
                 phd_start_year=wrap(2019, "2019 - Present"),
                 phd_year=wrap(2026, "2019 - Present"))
    assert withheld(result)


def test_research_scholar_on_the_line_below_the_degree_is_withheld():
    """
    A stage word one line down is still the candidate's own entry. It is
    also why a line carrying a stage word is never discarded as a section
    heading - "Research Scholar" would otherwise be eaten by "Research".
    """
    result = run("""Imran Qureshi

Education
Ph.D. Electronics, IIT Kanpur
Research Scholar, 2018 onwards
""",
                 phd_status=wrap("awarded", "Ph.D. Electronics, IIT Kanpur"),
                 phd_start_year=wrap(2018, "2018"),
                 phd_year=wrap(2024, "2024"))
    assert withheld(result)


def test_a_withheld_claim_is_not_reported_as_missing_evidence():
    """
    "We could not confirm this" and "there is nothing here" are different
    statements, and a reviewer acts on them differently.
    """
    result = run("""Arpit Singh Yadav

Academic Qualifications
2020   -  Ph.D.    Indian Institute of Information Technology
Ph.D. Research Scholar, 2020 - Present
""",
                 phd_status=wrap("awarded", "2020  -  Ph.D.  Indian Institute"),
                 phd_start_year=wrap(2020, "2020  -  Ph.D.  Indian Institute"),
                 phd_year=wrap(2020, "2020  -  Ph.D.  Indian Institute"))
    criterion = scoring.score_doctorate(result["profile"], gate(result))
    assert criterion.points == 0
    assert criterion.insufficient_evidence is False, \
        "a withheld claim is not an absent one"


# ---------------------------------------------------------------------------
# Completion without the word "awarded"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("degree_line, span, start, end", [
    ("Ph.D. in CSE, IIT Delhi                   2015 - 2022",
     "Ph.D. in CSE, IIT Delhi", 2015, 2022),
    ("Doctor of Philosophy (Computer Science), IISc   2014 - 2019",
     "Doctor of Philosophy (Computer Science), IISc", 2014, 2019),
    ("PhD awarded in 2022, University of Hyderabad",
     "PhD awarded in 2022, University of Hyderabad", 2017, 2022),
    ("D.Phil. Engineering Science, Oxford       2013 - 2018",
     "D.Phil. Engineering Science, Oxford", 2013, 2018),
    ("Completed doctoral degree in Mathematics, IIT Madras, 2021",
     "Completed doctoral degree in Mathematics, IIT Madras, 2021", 2016, 2021),
])
def test_a_completed_doctorate_needs_no_literal_awarded_wording(
        degree_line, span, start, end):
    """
    The validator must not downgrade a finished degree merely because the
    CV never prints the word "awarded". None of these say it.
    """
    # The year fields quote the whole line, because a year has to be
    # traceable to the span that carries it or check_scalar drops it.
    result = run(f"Candidate\n\nEducation\n{degree_line}\n",
                 phd_status=wrap("awarded", span),
                 phd_start_year=wrap(start, degree_line),
                 phd_year=wrap(end, degree_line))
    assert not withheld(result), f"withheld: {degree_line}"
    assert gate(result)["outcome"] == "met"


@pytest.mark.parametrize("degree_line, span", [
    ("Ph.D. Computer Science, IIT Delhi   2019 - Present", "Ph.D. Computer Science, IIT Delhi"),
    ("Ph.D. (pursuing), Anna University   2021 - ", "Ph.D. (pursuing), Anna University"),
    ("Ph.D. Chemistry, IISER - ongoing", "Ph.D. Chemistry, IISER - ongoing"),
    ("Ph.D. Mechanical Engineering, expected 2027", "Ph.D. Mechanical Engineering, expected 2027"),
])
def test_an_unfinished_doctorate_is_never_passed_as_complete(degree_line, span):
    result = run(f"Candidate\n\nEducation\n{degree_line}\n",
                 phd_status=wrap("awarded", span),
                 phd_start_year=wrap(2019, span),
                 phd_year=wrap(2026, span))
    assert withheld(result), f"NOT withheld: {degree_line}"
    assert gate(result)["outcome"] != "met"


# ---------------------------------------------------------------------------
# A negative claim, and whose doctorate the CV is talking about
# ---------------------------------------------------------------------------

def test_a_referees_doctorate_is_not_the_candidates():
    """
    Same scope error, opposite direction. A master's-only candidate was
    reported as "doctorate evidenced" because a referee has a PhD.
    """
    result = run("""Anita Rao
M.Tech, NIT Warangal, 2016 - 2018

References
Prof. Soumen Bag, Ph.D., IIT Dhanbad
Prof. Rajarshi Pal, Ph.D., IDRBT Hyderabad
""", phd_status=wrap("none", ""),
        masters_degree=wrap("M.Tech", "M.Tech, NIT Warangal, 2016 - 2018"))
    assert gate(result)["outcome"] == "not_met"
    assert result["profile"]["phd_status"]["value"] == "none"


def test_a_supervised_students_doctorate_is_not_the_candidates():
    result = run("""Anita Rao
M.Tech, NIT Warangal

Research Student Supervision (Ph.D.)
Ongoing   Mr. Shailesh Kumar, IIT ISM Dhanbad
""", phd_status=wrap("none", ""))
    assert gate(result)["outcome"] == "not_met"


def test_a_doctorate_under_education_still_contradicts_a_none_claim():
    """The safety net the scoping must not remove."""
    result = run("""Anita Rao

Education
Ph.D. Physics, IISc Bangalore    2015 - 2021
""", phd_status=wrap("none", ""))
    assert result["profile"]["phd_status"]["value"] is None
    assert gate(result)["outcome"] == "indeterminate"


# ---------------------------------------------------------------------------
# Institution naming is a separate question from completion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("institution", [
    "Indian Institute of Technology (Indian School of Mines) Dhanbad",
    "IIT Dhanbad",
    "I.I.T. Dhanbad",
    "Chhattisgarh Swami Vivekanand Technical University",
    "University of Hyderabad, Telangana, India",
])
def test_completion_does_not_depend_on_recognising_the_institution(institution):
    """
    Whether we can classify the awarding body and whether the degree is
    finished are independent facts. An unrecognised university must not
    cost a candidate their doctorate - it affects only the institution
    multiplier, which is a separate, visible factor.
    """
    span = f"Ph.D. Computer Science, {institution}"
    result = run(f"Candidate\n\nEducation\n{span}    2015 - 2022\n",
                 phd_status=wrap("awarded", span),
                 phd_institution=wrap(institution, institution),
                 phd_start_year=wrap(2015, "2015 - 2022"),
                 phd_year=wrap(2022, "2015 - 2022"))
    assert not withheld(result)
    assert gate(result)["outcome"] == "met"


# ---------------------------------------------------------------------------
# Completion years for the other degrees
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("span, extracted, expected", [
    ("2013 - 2015", 2013, 2015),
    ("2008 - 2012", 2008, 2012),
    ("Aug 2015 - May 2021", 2015, 2021),
    ("2017 to 2019", 2017, 2019),
])
def test_a_range_yields_the_completion_year_not_the_start_year(
        span, extracted, expected):
    result = run(f"Candidate\n\nEducation\nM.Tech, NIT Trichy  {span}\n",
                 masters_degree=wrap("M.Tech", "M.Tech, NIT Trichy"),
                 masters_year=wrap(extracted, span))
    assert result["profile"]["masters_year"]["value"] == expected


def test_a_single_year_row_is_left_alone():
    """
    One year is not a range, and guessing which end it is would be
    inventing a fact. It stays as extracted.
    """
    result = run("Candidate\n\nEducation\nM.Tech, NIT Trichy   2019\n",
                 masters_degree=wrap("M.Tech", "M.Tech, NIT Trichy"),
                 masters_year=wrap(2019, "M.Tech, NIT Trichy   2019"))
    assert result["profile"]["masters_year"]["value"] == 2019


# ---------------------------------------------------------------------------
# Scope, tested directly
# ---------------------------------------------------------------------------

def test_the_scope_stops_at_the_next_section():
    detail = run(SUPERVISOR_CV, **SUPERVISOR_PROFILE)["scalar_detail"]
    context = validate.doctorate_context(detail, SUPERVISOR_CV)
    assert "computer science and engineering" in context
    assert "ongoing" not in context
    assert "suspicious behavior" not in context


def test_an_untraceable_doctorate_row_reads_no_context_at_all():
    """
    With no anchor there is no evidence about the stage. Falling back to
    the whole document is the bug, so "" is the right answer - and the
    row is already unverified for failing traceability.
    """
    cv = "Candidate\n\nEducation\nM.Tech, NIT Trichy  2015 - 2017\n"
    detail = run(cv, phd_status=wrap("awarded", "Ph.D. from a place not in the CV"))
    assert validate.doctorate_context(detail["scalar_detail"], cv) == ""
    assert detail["profile"]["phd_status"]["value"] is None


# ---------------------------------------------------------------------------
# The real document
# ---------------------------------------------------------------------------

CV_PDF = ROOT / "data" / "cvs" / "Candidate_08.pdf"


@pytest.mark.skipif(not CV_PDF.exists(), reason="Candidate_08.pdf not present")
def test_candidate_08_pdf_text_still_carries_the_trap():
    """
    Pins the PDF, not the pipeline: if the text ever stops containing a
    doctorate entry and a separate supervision list, this file's premise
    is gone and the regression above is no longer testing anything.
    """
    import extract_text
    text = extract_text.extract_file(CV_PDF).text.lower()
    assert "ph.d. computer science and engineering" in text
    assert "ongoing" in text
    assert "research student supervision" in text
