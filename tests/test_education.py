"""
The revised rubric: education, institution tiers, branch relevance, rank.

EVERY FIXTURE HERE IS SYNTHETIC. No test reads a demo CV, a real
candidate id or a real person's degree, and test_no_candidate_specific_
logic asserts that the production modules contain none either. A rule
that only works on the six CVs in data/ is the failure this file exists
to catch.

Run:  python -m pytest tests/test_education.py -v
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import degrees        # noqa: E402
import institutions   # noqa: E402
import roles          # noqa: E402
import scoring        # noqa: E402

AS_OF = date(2026, 10, 15)


def wrap(value, evidence="evidence text"):
    return {"value": value, "evidence": evidence}


def validated(profile=None, positions=None):
    profile = dict(profile or {})
    profile["positions"] = positions or []
    return {"candidate_id": "Synthetic_Test", "profile": profile,
            "validation_summary": {}}


def verified(pubs=None):
    return {"candidate_id": "Synthetic_Test", "publications": pubs or []}


def role(title, start, end=None, category="teaching", current=False,
         organisation="An Institution", section=""):
    return {"title": title, "organisation": organisation,
            "category": category, "start_date": start, "end_date": end,
            "is_current": current, "source_section": section,
            "evidence": f"{title} {organisation} {start}"}


def criterion(result, name):
    return next(c for c in result["criteria"] if c["criterion"] == name)


def score(profile=None, positions=None, pubs=None, department="cse"):
    return scoring.score_candidate(
        validated(profile, positions), verified(pubs),
        as_of=AS_OF, department=department)


# ---------------------------------------------------------------------------
# The rubric's own arithmetic
# ---------------------------------------------------------------------------

def test_weights_sum_to_one_hundred():
    assert sum(scoring.WEIGHTS.values()) == 100


def test_the_three_approved_categories_hold_their_mandated_totals():
    assert scoring.CATEGORY_TOTALS == {
        "research": 40, "experience": 25, "education": 35}


def test_education_subweights_match_the_approved_table():
    assert scoring.WEIGHTS["doctorate"] == 15
    assert scoring.WEIGHTS["postdoctoral"] == 5
    assert scoring.WEIGHTS["masters"] == 8
    assert scoring.WEIGHTS["bachelors"] == 7


def test_teaching_outweighs_industry_as_the_policy_requires():
    assert scoring.WEIGHTS["teaching_experience"] == 20
    assert scoring.WEIGHTS["industry_experience"] == 5
    assert (scoring.WEIGHTS["teaching_experience"]
            > scoring.WEIGHTS["industry_experience"] * 3)


def test_every_criterion_reports_its_category():
    result = score()
    for entry in result["criteria"]:
        assert entry["category"] in scoring.CATEGORIES


def test_category_scores_sum_to_the_total():
    result = score({"phd_status": wrap("awarded"), "phd_year": wrap(2020),
                    "phd_institution": wrap("IIT Delhi"),
                    "masters_degree": wrap("M.Tech"),
                    "masters_subject": wrap("Computer Science")})
    earned = sum(c["earned"] for c in result["category_scores"].values())
    assert round(earned, 2) == result["total_score"]


def test_no_candidate_specific_logic_in_the_new_modules():
    """
    The generalisation guard. A rule that names a candidate, a demo CV or
    a specific person is a rule that works on one resume.
    """
    banned = ("candidate_0", "candidate0", "deepali", "shiva", "ayush",
              "arpit", "vikas", "kumawat", "dixit")
    for module in ("degrees.py", "institutions.py", "roles.py"):
        text = (ROOT / "src" / module).read_text(encoding="utf-8").lower()
        for token in banned:
            assert token not in text, f"{module} references {token!r}"


# ---------------------------------------------------------------------------
# Institution classification - variants, lookalikes, the unknown
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,tier", [
    # Tier 1 - the seven first-generation IITs, however they are spelled.
    ("IIT Bombay", "tier_1"),
    ("Indian Institute of Technology Bombay", "tier_1"),
    ("I.I.T. Bombay", "tier_1"),
    ("IIT-Kanpur", "tier_1"),
    ("iit kharagpur", "tier_1"),
    ("IIT Delhi", "tier_1"),
    ("Indian Institute of Technology, Chennai", "tier_1"),
    ("IIT Roorkee", "tier_1"),
    ("IIT Guwahati", "tier_1"),
    ("IITKGP", "tier_1"),
    # Tier 1 - IISc and the top NITs.
    ("Indian Institute of Science", "tier_1"),
    ("IISc Bangalore", "tier_1"),
    ("NIT Warangal", "tier_1"),
    ("National Institute of Technology, Trichy", "tier_1"),
    ("NIT Surathkal", "tier_1"),
    ("National Institute of Technology Karnataka", "tier_1"),
    ("NIT Rourkela", "tier_1"),
    ("NIT Calicut", "tier_1"),
    # Tier 1 - the established peers, which the re-tiering left in place.
    ("IIIT Hyderabad", "tier_1"),
    ("BITS Pilani", "tier_1"),
    ("Birla Institute of Technology and Science, Pilani", "tier_1"),
    ("Indian Statistical Institute", "tier_1"),
    ("Massachusetts Institute of Technology", "tier_1"),
    ("Nanyang Technological University", "tier_1"),
    # Tier 2 - every other IIT, including the two converted institutes.
    ("IIT Hyderabad", "tier_2"),
    ("IIT Ropar", "tier_2"),
    ("Indian Institute of Technology Gandhinagar", "tier_2"),
    ("IIT (BHU) Varanasi", "tier_2"),
    ("Indian Institute of Technology (Indian School of Mines) Dhanbad",
     "tier_2"),
    # Tier 2 - every other NIT, by city and by named-institute form.
    ("NIT Delhi", "tier_2"),
    ("NIT Durgapur", "tier_2"),
    ("MNNIT Allahabad", "tier_2"),
    ("Visvesvaraya National Institute of Technology, Nagpur", "tier_2"),
    ("SVNIT Surat", "tier_2"),
    # Tier 3 - other recognised degree-awarding institutions.
    ("Central University of Rajasthan", "tier_3"),
    ("University of Delhi", "tier_3"),
    ("Delhi Technological University", "tier_3"),
    ("Anna University", "tier_3"),
    ("Banaras Hindu University", "tier_3"),
    ("University of Hyderabad", "tier_3"),
    ("Jadavpur University", "tier_3"),
])
def test_known_institutions_and_their_spelling_variants(name, tier):
    assert institutions.classify(name)["tier"] == tier


@pytest.mark.parametrize("name", [
    "Some Private College of Engineering",
    "Shri Ram Institute",
    "Greenfield University of Technology",
])
def test_an_unlisted_institution_is_flagged_not_punished(name):
    """
    'We have no entry for this college' and 'this college is weak' are
    different statements, and only the first one is supported. The second
    would be a silent penalty, which the policy forbids.
    """
    verdict = institutions.classify(name)
    assert verdict["tier"] == "unknown"
    assert verdict["needs_review"] is True
    assert verdict["multiplier"] > 0, "an unknown institution is never zeroed"
    assert "gap in our list" in verdict["reason"]


def test_an_absent_institution_is_distinct_from_an_unknown_one():
    absent = institutions.classify(None)
    unknown = institutions.classify("Nowhere College")
    assert absent["tier"] == "absent"
    assert unknown["tier"] == "unknown"
    assert absent["reason"] != unknown["reason"]


@pytest.mark.parametrize("lookalike,why", [
    ("BIT Mesra", "a distinct institution from BITS Pilani"),
    ("Birla Institute of Technology, Mesra", "distinct from BITS Pilani"),
    ("National Institute of Fashion Technology", "NIFT is not an NIT"),
    ("NIFT Delhi", "NIFT is not an NIT"),
])
def test_a_lookalike_name_cannot_claim_tier_one(lookalike, why):
    """A famous acronym must not be credited to an unrelated institution."""
    assert institutions.classify(lookalike)["tier"] != "tier_1", why


def test_renamed_and_merged_institutions_resolve_to_their_current_name():
    assert institutions.classify("University of Roorkee")["tier"] == "tier_1"
    assert institutions.classify("Delhi College of Engineering")["tier"] == "tier_3"
    assert institutions.classify(
        "Bengal Engineering and Science University")["tier"] == "tier_1"


def test_the_classification_rules_are_visible_to_an_administrator():
    rubric = institutions.visible_rubric()
    assert rubric["multipliers"]["tier_1"] == 1.0
    assert rubric["tiers"]["tier_1"]["patterns"], "an admin must see the rules"
    assert "never silently" in rubric["unknown_policy"]


# ---------------------------------------------------------------------------
# Degree level, equivalence, integrated and dual degrees
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,level", [
    ("B.Tech", "bachelors"), ("B.E.", "bachelors"), ("BTech", "bachelors"),
    ("B.Sc", "bachelors"), ("BCA", "bachelors"), ("BS", "bachelors"),
    ("M.Tech", "masters"), ("M.E.", "masters"), ("MCA", "masters"),
    ("M.Sc", "masters"), ("MS", "masters"), ("M.Phil", "masters"),
    ("Ph.D.", "doctorate"), ("PhD", "doctorate"),
    ("Doctor of Philosophy", "doctorate"),
])
def test_degree_level_is_read_from_the_qualification_name(name, level):
    assert degrees.level_of(name) == level


@pytest.mark.parametrize("name,ratio", [
    ("B.Tech", 1.00), ("B.E.", 1.00), ("BCA", 0.85), ("B.Sc", 0.75),
])
def test_bachelor_equivalence_ratios(name, ratio):
    assert degrees.equivalence(name, "bachelors")[0] == ratio


@pytest.mark.parametrize("name,ratio", [
    ("M.Tech", 1.00), ("M.E.", 1.00), ("MS", 1.00),
    ("MCA", 0.85), ("M.Sc", 0.80), ("M.Phil", 0.80),
])
def test_master_equivalence_ratios(name, ratio):
    assert degrees.equivalence(name, "masters")[0] == ratio


def test_an_unrecognised_qualification_is_flagged_not_zeroed():
    ratio, _, needs_review = degrees.equivalence("Licenciatura", "bachelors")
    assert ratio > 0
    assert needs_review is True


@pytest.mark.parametrize("name", [
    "Integrated M.Tech", "Dual Degree B.Tech-M.Tech",
    "B.Tech + M.Tech Dual Degree", "Integrated BS-MS", "5-year Integrated M.Sc",
])
def test_integrated_and_dual_programmes_are_recognised(name):
    assert degrees.is_integrated(name) is True


def test_an_integrated_masters_also_confers_the_bachelors_category():
    result = score({
        "masters_degree": wrap("Integrated M.Tech"),
        "masters_institution": wrap("IIT Madras"),
        "masters_subject": wrap("Computer Science and Engineering"),
    })
    assert criterion(result, "masters")["points"] == 8.0
    bachelors = criterion(result, "bachelors")
    assert bachelors["points"] > 0, \
        "a five-year integrated programme confers both levels"
    assert "integrated" in bachelors["note"].lower()


def test_a_separately_listed_bachelors_is_not_credited_twice():
    """
    Holding both a B.Tech and an integrated M.Tech must not exceed the
    bachelor's weight. Each category takes one qualification and is
    capped at its own marks.
    """
    result = score({
        "bachelors_degree": wrap("B.Tech"),
        "bachelors_institution": wrap("IIT Delhi"),
        "bachelors_subject": wrap("Computer Science"),
        "masters_degree": wrap("Integrated M.Tech"),
        "masters_institution": wrap("IIT Delhi"),
        "masters_subject": wrap("Computer Science"),
    })
    assert criterion(result, "bachelors")["points"] <= scoring.WEIGHTS["bachelors"]
    assert criterion(result, "masters")["points"] <= scoring.WEIGHTS["masters"]


def test_no_education_criterion_can_exceed_its_weight():
    result = score({
        "phd_status": wrap("awarded"), "phd_year": wrap(2015),
        "phd_institution": wrap("IIT Bombay"),
        "masters_degree": wrap("M.Tech"), "masters_institution": wrap("IIT Bombay"),
        "masters_subject": wrap("Computer Science and Engineering"),
        "bachelors_degree": wrap("B.Tech"), "bachelors_institution": wrap("IIT Bombay"),
        "bachelors_subject": wrap("Computer Science and Engineering"),
    })
    for name in ("doctorate", "postdoctoral", "masters", "bachelors"):
        entry = criterion(result, name)
        assert entry["points"] <= entry["weight"]
    assert result["category_scores"]["education"]["earned"] <= 35


# ---------------------------------------------------------------------------
# Branch relevance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("subject,verdict", [
    ("Computer Science and Engineering", "core"),
    ("Information Technology", "core"),
    ("Artificial Intelligence", "core"),
    ("Electronics and Communication Engineering", "adjacent"),
    ("Mathematics", "adjacent"),
    ("Civil Engineering", "distant"),
    ("History", "distant"),
])
def test_branch_relevance_against_a_cse_post(subject, verdict):
    assert degrees.branch_relevance(subject, "cse")["verdict"] == verdict


def test_branch_relevance_follows_the_advertised_department():
    """The same subject is core for one post and adjacent for another."""
    assert degrees.branch_relevance("Electronics", "ece")["verdict"] == "core"
    assert degrees.branch_relevance("Electronics", "cse")["verdict"] == "adjacent"


def test_a_distant_subject_is_lifted_by_documented_research_in_the_field():
    """
    The policy: a less-related branch must not be treated as irrelevant
    when the evidence shows strong alignment.
    """
    plain = degrees.branch_relevance("Civil Engineering", "cse")
    lifted = degrees.branch_relevance(
        "Civil Engineering", "cse",
        supporting_evidence="machine learning for structural health monitoring")
    assert plain["verdict"] == "distant"
    assert lifted["verdict"] == "adjacent"
    assert lifted["lifted"] is True
    assert lifted["ratio"] > plain["ratio"]


def test_documented_research_can_never_lift_a_subject_to_core():
    lifted = degrees.branch_relevance(
        "History", "cse", supporting_evidence="computer science deep learning")
    assert lifted["verdict"] != "core", \
        "work in a field is evidence of capability, not a qualification in it"


def test_an_unreadable_subject_is_not_charged_to_the_candidate():
    verdict = degrees.branch_relevance(None, "cse")
    assert verdict["ratio"] == 1.00
    assert verdict["needs_review"] is True


def test_branch_relevance_is_applied_once_and_only_to_the_two_degree_marks():
    """
    The anti-double-counting guarantee. Changing the subject must move the
    bachelor's and master's marks and nothing else.
    """
    common = {
        "phd_status": wrap("awarded"), "phd_year": wrap(2020),
        "phd_institution": wrap("IIT Delhi"),
        "masters_degree": wrap("M.Tech"), "masters_institution": wrap("IIT Delhi"),
        "bachelors_degree": wrap("B.Tech"), "bachelors_institution": wrap("IIT Delhi"),
    }
    positions = [role("Assistant Professor", "2021-01", "2024-01")]
    cse = score({**common, "masters_subject": wrap("Computer Science"),
                 "bachelors_subject": wrap("Computer Science")}, positions)
    civil = score({**common, "masters_subject": wrap("Civil Engineering"),
                   "bachelors_subject": wrap("Civil Engineering")}, positions)

    moved = {c["criterion"] for c in cse["criteria"]
             if c["points"] != criterion(civil, c["criterion"])["points"]}
    assert moved == {"masters", "bachelors"}


# ---------------------------------------------------------------------------
# Academic rank
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,rank", [
    ("Assistant Professor", "assistant_professor"),
    ("Asst. Professor", "assistant_professor"),
    ("Associate Professor", "associate_professor"),
    ("Assoc. Prof.", "associate_professor"),
    ("Reader", "associate_professor"),
    ("Senior Lecturer", "associate_professor"),
    ("Professor", "professor"),
    ("Full Professor", "professor"),
    ("Lecturer", "faculty"),
    ("Visiting Professor", "faculty"),
    ("Adjunct Professor", "faculty"),
])
def test_academic_rank_is_read_from_the_title(title, rank):
    assert roles.faculty_rank({"title": title})[0] == rank


def test_an_associate_professorship_outscores_an_assistant_one():
    """The policy requirement, stated directly."""
    span = ("2020-01", "2024-01")
    assistant = score(positions=[role("Assistant Professor", *span)])
    associate = score(positions=[role("Associate Professor", *span)])
    professor = score(positions=[role("Professor", *span)])

    a = criterion(assistant, "teaching_experience")["points"]
    b = criterion(associate, "teaching_experience")["points"]
    c = criterion(professor, "teaching_experience")["points"]
    assert a < b < c


def test_rank_never_promotes_an_assistantship():
    """
    teaching_kind() decides whether a role counts; rank only scales what
    it is worth. A TA-ship must stay at half rate whatever its wording.
    """
    span = ("2020-01", "2024-01")
    ta = score(positions=[role("Teaching Assistant", *span)])
    lecturer = score(positions=[role("Lecturer", *span)])
    assert (criterion(ta, "teaching_experience")["points"]
            == pytest.approx(
                criterion(lecturer, "teaching_experience")["points"] * 0.5))


def test_faculty_rank_refuses_to_answer_for_an_assistantship():
    with pytest.raises(ValueError):
        roles.faculty_rank({"title": "Teaching Assistant"})


def test_concurrent_ranks_cannot_bill_the_same_month_twice():
    """
    Overlapping appointments are merged, not summed. Holding an Associate
    and an Assistant professorship in the same months must cost one
    month of wall-clock time, at the better rate.
    """
    both = score(positions=[
        role("Associate Professor", "2020-01", "2024-01"),
        role("Assistant Professor", "2020-01", "2024-01", organisation="Other"),
    ])
    associate_only = score(positions=[
        role("Associate Professor", "2020-01", "2024-01")])
    assert (criterion(both, "teaching_experience")["points"]
            == criterion(associate_only, "teaching_experience")["points"])


def test_a_promotion_counts_each_period_at_the_rank_actually_held():
    """Assistant then Associate: four years total, priced by period."""
    promoted = score(positions=[
        role("Assistant Professor", "2018-01", "2020-01"),
        role("Associate Professor", "2020-01", "2022-01"),
    ])
    flat = score(positions=[role("Assistant Professor", "2018-01", "2022-01")])
    assert (criterion(promoted, "teaching_experience")["points"]
            > criterion(flat, "teaching_experience")["points"])


# ---------------------------------------------------------------------------
# Postdoctoral experience
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Postdoctoral Fellow", "Post-Doctoral Researcher", "Postdoc",
    "Post Doctoral Associate", "National Postdoctoral Fellow",
])
def test_postdoctoral_titles_are_recognised(title):
    assert roles.is_postdoc({"title": title})[0] is True


@pytest.mark.parametrize("title", [
    "Research Scholar", "PhD Student", "Junior Research Fellow",
    "Senior Research Fellow", "Research Associate", "Research Scientist",
    "Project Associate", "Assistant Professor",
])
def test_a_postdoc_is_not_confused_with_a_phd_or_ordinary_research_work(title):
    assert roles.is_postdoc({"title": title})[0] is False


def test_a_postdoc_listed_only_under_a_section_heading_is_still_found():
    assert roles.is_postdoc({
        "title": "Department of Computing",
        "source_section": "Postdoctoral Experience"})[0] is True


def test_postdoctoral_duration_scores_against_the_saturation_point():
    one_year = score(positions=[
        role("Postdoctoral Fellow", "2023-01", "2024-01", category="research")])
    two_years = score(positions=[
        role("Postdoctoral Fellow", "2022-01", "2024-01", category="research")])
    assert criterion(one_year, "postdoctoral")["points"] == pytest.approx(2.5)
    assert criterion(two_years, "postdoctoral")["points"] == 5.0


def test_overlapping_postdocs_are_merged_not_summed():
    overlapping = score(positions=[
        role("Postdoctoral Fellow", "2022-01", "2024-01", category="research"),
        role("Postdoctoral Researcher", "2022-01", "2024-01",
             category="research", organisation="Second Institution"),
    ])
    assert criterion(overlapping, "postdoctoral")["measured"] == 2.0


def test_an_ongoing_postdoc_is_measured_against_the_recorded_run_date():
    ongoing = score(positions=[
        role("Postdoctoral Fellow", "2025-10", None, category="research",
             current=True)])
    assert criterion(ongoing, "postdoctoral")["measured"] == 1.0


def test_a_postdoc_without_a_completed_doctorate_is_flagged_not_discounted():
    result = score(
        {"phd_status": wrap("pursuing")},
        [role("Postdoctoral Fellow", "2022-01", "2024-01", category="research")])
    postdoc = criterion(result, "postdoctoral")
    assert postdoc["points"] == 5.0, "flagged, not silently discounted"
    assert postdoc["needs_review"] is True


def test_no_postdoc_scores_zero_and_says_why():
    postdoc = criterion(score(), "postdoctoral")
    assert postdoc["points"] == 0.0
    assert postdoc["insufficient_evidence"] is True
    assert "insufficient evidence" in postdoc["note"]


# ---------------------------------------------------------------------------
# Completed vs ongoing doctorates
# ---------------------------------------------------------------------------

def test_an_ongoing_phd_earns_no_doctorate_marks_however_good_the_institution():
    """A tier-1 name scales a doctorate; it cannot create one."""
    ongoing = score({"phd_status": wrap("pursuing"),
                     "phd_institution": wrap("IIT Bombay")})
    assert criterion(ongoing, "doctorate")["points"] == 0.0


def test_a_completed_phd_is_scaled_by_its_institution():
    def phd(institution):
        return criterion(score({"phd_status": wrap("awarded"),
                                "phd_year": wrap(2020),
                                "phd_institution": wrap(institution)}),
                         "doctorate")

    # One award, three tiers. The ordering is the whole point of the
    # hierarchy: tier 1 highest, tier 2 lower, tier 3 the base rate.
    assert phd("IIT Bombay")["points"] == 15.0
    assert phd("IIT Hyderabad")["points"] == pytest.approx(12.75)
    assert phd("University of Delhi")["points"] == pytest.approx(10.5)

    unknown = phd("Nowhere College")
    assert unknown["points"] == pytest.approx(10.5), \
        "an unrecognised name earns the base rate, never less"
    assert unknown["needs_review"] is True


def test_the_eligibility_gate_stays_separate_from_the_education_marks():
    """
    A gate decides appointability; the marks measure merit. Neither may
    read the other.
    """
    net_only = score({"net_qualified": wrap(True)})
    assert net_only["eligibility"]["outcome"] == "met"
    assert criterion(net_only, "doctorate")["points"] == 0.0
    assert net_only["eligibility"]["affects_score"] is False


# ---------------------------------------------------------------------------
# Missing, contradictory and unverifiable information
# ---------------------------------------------------------------------------

def test_a_missing_degree_is_reported_as_unread_not_as_absent():
    bachelors = criterion(score(), "bachelors")
    assert bachelors["points"] == 0.0
    assert bachelors["insufficient_evidence"] is True
    assert bachelors["needs_review"] is True
    assert "not the same as a finding" in bachelors["note"]


def test_every_education_factor_is_reported_by_name():
    """
    A reviewer who disagrees with a score has to see WHICH factor they
    disagree with, so the multipliers are never collapsed into one число.
    """
    masters = criterion(score({
        "masters_degree": wrap("M.Sc"),
        "masters_institution": wrap("Nowhere College"),
        "masters_subject": wrap("Chemistry"),
    }), "masters")
    named = {f["factor"] for f in masters["factors"]}
    assert named == {"equivalence", "institution", "branch"}
    for factor in masters["factors"]:
        assert factor["reason"], "every factor states its reason"


def test_the_three_education_factors_multiply_as_documented():
    masters = criterion(score({
        "masters_degree": wrap("M.Sc"),              # 0.80
        "masters_institution": wrap("Nowhere College"),   # 0.70
        "masters_subject": wrap("Civil Engineering"),     # 0.50
    }), "masters")
    assert masters["points"] == pytest.approx(8 * 0.80 * 0.70 * 0.50)


def test_scoring_stays_reproducible_from_the_same_evidence():
    profile = {"phd_status": wrap("awarded"), "phd_year": wrap(2020),
               "phd_institution": wrap("IIT Delhi"),
               "masters_degree": wrap("M.Tech"),
               "masters_subject": wrap("Computer Science")}
    positions = [role("Associate Professor", "2020-01", "2024-01")]
    first = score(profile, positions)
    second = score(profile, positions)
    assert first["total_score"] == second["total_score"]
    assert [c["points"] for c in first["criteria"]] == \
           [c["points"] for c in second["criteria"]]


def test_the_department_is_recorded_so_a_score_cannot_be_misread():
    assert score(department="ece")["rubric"]["department"] == "ece"


# ---------------------------------------------------------------------------
# Whole-profile shapes the policy explicitly asks about
# ---------------------------------------------------------------------------

STRONG_TEACHER = {
    "profile": {"phd_status": wrap("awarded"), "phd_year": wrap(2012),
                "phd_institution": wrap("University of Delhi"),
                "masters_degree": wrap("M.Tech"),
                "masters_institution": wrap("NIT Warangal"),
                "masters_subject": wrap("Computer Science and Engineering"),
                "bachelors_degree": wrap("B.Tech"),
                "bachelors_institution": wrap("NIT Warangal"),
                "bachelors_subject": wrap("Computer Science and Engineering")},
    "positions": [role("Associate Professor", "2013-01", "2026-01")],
}

STRONG_RESEARCHER = {
    "profile": {"phd_status": wrap("awarded"), "phd_year": wrap(2022),
                "phd_institution": wrap("IIT Bombay"),
                "masters_degree": wrap("M.Tech"),
                "masters_institution": wrap("IIT Bombay"),
                "masters_subject": wrap("Computer Science and Engineering"),
                "bachelors_degree": wrap("B.Tech"),
                "bachelors_institution": wrap("IIT Bombay"),
                "bachelors_subject": wrap("Computer Science and Engineering")},
    "positions": [role("Assistant Professor", "2024-01", "2026-01")],
}


def _pub(title, citations=0):
    return {"title": title, "status": "published", "credit_exclusions": [],
            "verification": {"outcome": "matched", "cited_by_count": citations,
                             "openalex_id": "https://openalex.org/W1",
                             "query_url": "https://api.openalex.org/works?x"}}


def test_a_strong_teacher_with_few_papers_still_scores_substantially():
    result = score(STRONG_TEACHER["profile"], STRONG_TEACHER["positions"],
                   pubs=[_pub("A Paper")])
    assert result["category_scores"]["experience"]["earned"] == 20.0
    assert result["total_score"] > 40, \
        "a long teaching record must not be buried by a thin publication list"


def test_a_strong_researcher_with_little_teaching_still_scores_substantially():
    result = score(STRONG_RESEARCHER["profile"], STRONG_RESEARCHER["positions"],
                   pubs=[_pub(f"Paper {i}", citations=12) for i in range(20)])
    assert result["category_scores"]["research"]["earned"] == 40.0
    assert result["total_score"] > 40


def test_the_two_profiles_are_separated_by_evidence_not_by_a_thumb_on_the_scale():
    """
    Both shapes must be viable. Neither may be structurally incapable of
    competing, which is what a badly balanced rubric produces.

    The two fixtures carry DIFFERENT institution tiers, which is realistic
    but would make this measure institution standing as well as profile
    shape - and institution standing is meant to separate candidates, so
    it would mask the thing under test. The awarding institutions are
    therefore held equal here, and only the shape differs.
    """
    same_institution = "IIT Bombay"
    teacher = score({**STRONG_TEACHER["profile"],
                     "phd_institution": wrap(same_institution),
                     "masters_institution": wrap(same_institution),
                     "bachelors_institution": wrap(same_institution)},
                    STRONG_TEACHER["positions"], pubs=[_pub("A Paper")])
    researcher = score({**STRONG_RESEARCHER["profile"],
                        "phd_institution": wrap(same_institution),
                        "masters_institution": wrap(same_institution),
                        "bachelors_institution": wrap(same_institution)},
                       STRONG_RESEARCHER["positions"],
                       pubs=[_pub(f"P{i}", citations=12) for i in range(20)])
    assert abs(teacher["total_score"] - researcher["total_score"]) < 30


# ---------------------------------------------------------------------------
# Two defects found by running the real extractor, pinned here.
# ---------------------------------------------------------------------------

def test_an_enum_returned_instead_of_a_degree_name_is_recovered():
    """
    A live run reproduced KNOWN_DEFECTS.md section 2: the model returned
    the controlled-vocabulary word 'masters' where the qualification name
    belongs. Pricing that at the generic 'other' rate would cost a real
    M.Sc candidate marks for OUR extraction defect, so the name is read
    back out of the verbatim CV span the field already quotes.
    """
    name, note = degrees.name_from_evidence(
        "masters", "M.Sc. Physics, Banasthali University, 2014", "masters")
    assert name == "M.Sc"
    assert note and "placeholder" in note


def test_recovery_invents_nothing_when_the_cv_span_does_not_settle_it():
    assert degrees.name_from_evidence(
        "masters", "Postgraduate studies, 2014", "masters") == (None, None)


def test_a_real_name_is_never_overwritten_by_recovery():
    assert degrees.name_from_evidence("M.Tech", "anything", "masters") == \
        ("M.Tech", None)


def test_the_recovered_degree_scores_correctly_and_is_flagged():
    masters = criterion(score({
        "masters_degree": {"value": "masters",
                           "evidence": "M.Sc. Physics, Banasthali, 2014"},
        "masters_institution": wrap("IIT Delhi"),
        "masters_subject": wrap("Computer Science"),
    }), "masters")
    assert masters["points"] == pytest.approx(8 * 0.80)   # M.Sc, not "other"
    assert masters["needs_review"] is True


def test_an_institution_name_is_not_mangled_in_the_explanation():
    """
    str.capitalize() lower-cases the whole remainder and turned
    "awarded by IIT Delhi" into "awarded by iit delhi" in front of a
    reviewer. The institution is evidence; it must be spelled as the CV
    spells it.
    """
    import explain
    doctorate = criterion(score({
        "phd_status": wrap("awarded"), "phd_year": wrap(2020),
        "phd_institution": wrap("IIT Delhi"),
    }), "doctorate")
    prose = explain.template_explanation(doctorate)
    assert "IIT Delhi" in prose
    assert "iit delhi" not in prose


def test_an_education_explanation_decomposes_the_score():
    import explain
    prose = explain.template_explanation(criterion(score({
        "bachelors_degree": wrap("B.Sc"),
        "bachelors_institution": wrap("University of Delhi"),
        "bachelors_subject": wrap("Physics"),
        "bachelors_year": wrap(2012),
    }), "bachelors"))
    assert "B.Sc" in prose and "University of Delhi" in prose
    assert "0.75" in prose and "0.7" in prose and "0.5" in prose, \
        "a reviewer must be able to see every multiplier in the sentence"
    for internal in ("bachelors_degree", "_subject", "tier_3", "ratio"):
        assert internal not in prose


@pytest.mark.parametrize("background,subject", [
    ("cse", "Computer Science and Engineering"),
    ("ece", "Electronics and Communication Engineering"),
    ("interdisciplinary", "Computational Biology"),
    ("unrelated", "English Literature"),
])
def test_no_academic_background_is_structurally_excluded(background, subject):
    """
    A less-related branch loses branch marks. It must never lose the
    other 85 marks, or the rubric screens by discipline rather than merit.
    """
    result = score(
        {"phd_status": wrap("awarded"), "phd_year": wrap(2020),
         "phd_institution": wrap("IIT Delhi"),
         "masters_degree": wrap("M.Tech"), "masters_subject": wrap(subject),
         "masters_institution": wrap("IIT Delhi"),
         "bachelors_degree": wrap("B.Tech"), "bachelors_subject": wrap(subject),
         "bachelors_institution": wrap("IIT Delhi")},
        [role("Assistant Professor", "2021-01", "2026-01")],
        pubs=[_pub(f"P{i}", citations=10) for i in range(10)])
    assert result["total_score"] > 45, background
    assert criterion(result, "doctorate")["points"] == 15.0
