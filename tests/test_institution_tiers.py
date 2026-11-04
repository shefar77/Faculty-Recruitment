"""
The institution hierarchy: three tiers, applied the same way at every
qualification level.

WHAT THIS FILE PINS
    The approved change is that institution standing is graded, not flat:
    first-generation IITs, the top NITs and IISc earn full institution
    credit; the remaining IITs and NITs earn less; other recognised
    institutions earn the base rate. These tests pin the *structure* and
    the *rules*, and only as many individual institutions as are needed to
    show the split is real.

WHY THE MEMBERSHIP TESTS NAME INSTITUTIONS BUT THE CODE DOES NOT
    A tier list is a policy decision, so it lives in
    config/institutions.json and a maintainer edits it there. These tests
    read that configuration through institutions.classify(); they do not
    duplicate it. test_scoring_code_holds_no_institution_names asserts the
    scoring modules stay ignorant of every name.

EVERY FIXTURE IS SYNTHETIC. No test here reads a demo CV, a candidate id
or a real person's degree.

Run:  python -m pytest tests/test_institution_tiers.py -v
"""

from __future__ import annotations

import copy
import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import institutions   # noqa: E402
import scoring        # noqa: E402

AS_OF = date(2026, 10, 15)

# The seven first-generation institutes, and a sample of the rest. The
# "rest" list is a sample on purpose: the rule is "every other IIT", and a
# test that enumerated all of them would have to be edited whenever a new
# institute opens, which is exactly the maintenance the catch-all avoids.
FOUNDING_IITS = ["IIT Kharagpur", "IIT Bombay", "IIT Madras", "IIT Kanpur",
                 "IIT Delhi", "IIT Roorkee", "IIT Guwahati"]
OTHER_IITS = ["IIT Hyderabad", "IIT Gandhinagar", "IIT Ropar", "IIT Indore",
              "IIT Jodhpur", "IIT Mandi", "IIT Patna", "IIT Bhubaneswar",
              "IIT (BHU) Varanasi", "IIT (ISM) Dhanbad", "IIT Tirupati",
              "IIT Palakkad", "IIT Jammu", "IIT Bhilai", "IIT Goa",
              "IIT Dharwad"]
TOP_NITS = ["NIT Tiruchirappalli", "NIT Karnataka", "NIT Warangal",
            "NIT Rourkela", "NIT Calicut"]
OTHER_NITS = ["NIT Delhi", "NIT Durgapur", "NIT Silchar", "NIT Kurukshetra",
              "NIT Raipur", "MNNIT Allahabad", "MNIT Jaipur", "VNIT Nagpur",
              "SVNIT Surat", "MANIT Bhopal"]
OTHER_RECOGNISED = ["University of Delhi", "Anna University",
                    "Jadavpur University", "Banaras Hindu University",
                    "Delhi Technological University", "Osmania University",
                    "Central University of Rajasthan", "Thapar Institute"]

LEVELS = ("bachelors", "masters", "doctorate")


def wrap(value, evidence="evidence text"):
    return {"value": value, "evidence": evidence}


def profile_for(level, institution):
    """A minimal profile carrying exactly one qualification, at `level`."""
    if level == "doctorate":
        return {"phd_status": wrap("awarded"), "phd_year": wrap(2020),
                "phd_institution": wrap(institution)}
    degree = "B.Tech" if level == "bachelors" else "M.Tech"
    return {f"{level}_degree": wrap(degree),
            f"{level}_institution": wrap(institution),
            f"{level}_subject": wrap("Computer Science and Engineering"),
            f"{level}_year": wrap(2015)}


def score_level(level, institution, department="cse"):
    """Score one qualification and return its criterion dict."""
    profile = profile_for(level, institution)
    profile["positions"] = []
    result = scoring.score_candidate(
        {"candidate_id": "Synthetic_Test", "profile": profile,
         "validation_summary": {}},
        {"candidate_id": "Synthetic_Test", "publications": []},
        as_of=AS_OF, department=department)
    return next(c for c in result["criteria"] if c["criterion"] == level)


def institution_factors(criterion):
    return [f for f in criterion["factors"] if f["factor"] == "institution"]


# ---------------------------------------------------------------------------
# The shape of the hierarchy
# ---------------------------------------------------------------------------

def test_there_are_exactly_three_tiers_and_all_three_are_populated():
    tiers = institutions.config()["tiers"]
    assert list(tiers) == ["tier_1", "tier_2", "tier_3"]
    for name, tier in tiers.items():
        assert tier["patterns"], f"{name} has no patterns to match against"
        assert tier["label"] and tier["rationale"], \
            f"{name} must explain itself to a reviewer"


def test_credit_decreases_strictly_from_tier_one_to_tier_three():
    """
    The requirement is an ordering, not three arbitrary numbers: tier 1
    highest, tier 2 lower, tier 3 the base. A configuration that broke
    the ordering would invert the policy while still looking plausible.
    """
    m = institutions.config()["multipliers"]
    assert m["tier_1"] > m["tier_2"] > m["tier_3"] > 0


def test_an_unrecognised_name_earns_the_base_rate_and_never_less():
    """
    'Not in our list' is a gap in the list, not a finding about the
    institution, so it may never score below the lowest listed tier.
    """
    m = institutions.config()["multipliers"]
    assert m["unknown"] == m["tier_3"]
    verdict = institutions.classify("Greenfield College of Engineering")
    assert verdict["tier"] == "unknown"
    assert verdict["needs_review"] is True
    assert verdict["multiplier"] == m["tier_3"]


def test_no_tier_multiplier_can_inflate_a_qualification():
    """
    The multiplier scales marks down or leaves them alone. One above 1.0
    would let a famous name carry a qualification past its own category
    weight, which requirement 6 forbids.
    """
    for name, value in institutions.config()["multipliers"].items():
        assert 0 < value <= 1.0, f"{name} multiplier {value} is out of range"


# ---------------------------------------------------------------------------
# The IIT split
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", FOUNDING_IITS)
def test_a_first_generation_iit_is_tier_one(name):
    verdict = institutions.classify(name)
    assert verdict["tier"] == "tier_1"
    assert verdict["needs_review"] is False, \
        "a named first-generation campus is identified with certainty"


@pytest.mark.parametrize("name", OTHER_IITS)
def test_every_other_iit_is_tier_two(name):
    verdict = institutions.classify(name)
    assert verdict["tier"] == "tier_2"
    assert verdict["needs_review"] is False, \
        "a named campus is identified with certainty, so it is not flagged"


@pytest.mark.parametrize("name", FOUNDING_IITS)
def test_the_family_catch_all_cannot_demote_a_first_generation_iit(name):
    """
    Tier 2 ends in a pattern that matches ANY IIT. Pattern order is the
    only thing keeping it from swallowing the tier-1 campuses, so the
    order is worth a test of its own: spelled long or short, with an
    address trailing, a founding campus must still reach tier 1.
    """
    campus = name.replace("IIT ", "")
    for spelling in (name,
                     f"Indian Institute of Technology {campus}",
                     f"Indian Institute of Technology, {campus}, India",
                     f"I.I.T. {campus}"):
        assert institutions.classify(spelling)["tier"] == "tier_1", spelling


# ---------------------------------------------------------------------------
# The NIT split
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", TOP_NITS)
def test_a_top_nit_is_tier_one(name):
    assert institutions.classify(name)["tier"] == "tier_1"


@pytest.mark.parametrize("name", OTHER_NITS)
def test_every_other_nit_is_tier_two(name):
    verdict = institutions.classify(name)
    assert verdict["tier"] == "tier_2"
    assert verdict["needs_review"] is False


def test_a_top_nit_survives_its_long_and_abbreviated_spellings():
    for spelling in ("NIT Warangal", "N.I.T. Warangal",
                     "National Institute of Technology, Warangal",
                     "National Institute of Technology Warangal, Telangana"):
        assert institutions.classify(spelling)["tier"] == "tier_1", spelling
    for spelling in ("NITK", "NIT Surathkal",
                     "National Institute of Technology Karnataka"):
        assert institutions.classify(spelling)["tier"] == "tier_1", spelling


# ---------------------------------------------------------------------------
# Other recognised institutions, and the peers left in tier 1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", OTHER_RECOGNISED)
def test_other_recognised_institutions_are_tier_three(name):
    assert institutions.classify(name)["tier"] == "tier_3"


@pytest.mark.parametrize("name", [
    "Indian Institute of Science", "IISc Bangalore", "IIIT Hyderabad",
    "BITS Pilani", "Indian Statistical Institute",
    "Indian Institute of Science Education and Research Pune",
    "Tata Institute of Fundamental Research", "IIEST Shibpur",
    "Massachusetts Institute of Technology", "Stanford University",
    "University of Oxford", "ETH Zurich", "National University of Singapore",
])
def test_the_established_peers_stay_in_tier_one(name):
    """
    The approved reading of "top IITs, top NITs and IISc" names the Indian
    technical institutes without displacing the peers already classified
    alongside them. Left out, an MIT doctorate would score below a
    mid-tier NIT one, which no one intended.
    """
    assert institutions.classify(name)["tier"] == "tier_1"


# ---------------------------------------------------------------------------
# Honest uncertainty: the family is known, the campus is not
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "IIT", "Indian Institute of Technology", "I.I.T.",
    "NIT", "National Institute of Technology",
])
def test_a_campus_that_cannot_be_identified_is_flagged_not_guessed(name):
    """
    Requirement 4. "IIT" with no campus is certainly an IIT and the
    approved rule puts any non-founding IIT in tier 2 - but a
    first-generation campus written oddly would belong in tier 1, so the
    classification is not certain and a human is asked.
    """
    verdict = institutions.classify(name)
    assert verdict["tier"] == "tier_2", \
        "a statutory institute is not dropped to the base rate"
    assert verdict["needs_review"] is True
    assert "campus" in verdict["reason"].lower(), \
        "the reviewer must be told WHAT is uncertain"


def test_an_unidentified_campus_reaches_the_reviewer_through_the_score():
    """A flag that never leaves the classifier is not a flag."""
    criterion = score_level("doctorate", "Indian Institute of Technology")
    assert criterion["needs_review"] is True
    assert any("campus" in reason.lower()
               for reason in criterion["review_reasons"])


def test_an_absent_institution_is_not_treated_as_an_unidentified_campus():
    absent = institutions.classify(None)
    assert absent["tier"] == "absent"
    assert absent["needs_review"] is True


# ---------------------------------------------------------------------------
# Lookalikes must not reach the family catch-all
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lookalike,why", [
    ("National Institute of Fashion Technology", "NIFT is not an NIT"),
    ("NIFT Delhi", "NIFT is not an NIT"),
    ("NITTTR Chandigarh", "a teacher-training body is not an NIT"),
    ("National Institute of Technical Teachers Training and Research",
     "a teacher-training body is not an NIT"),
    ("BIT Mesra", "BIT Mesra is not BITS Pilani"),
    ("Birla Institute of Technology, Mesra", "BIT Mesra is not BITS Pilani"),
])
def test_a_lookalike_cannot_claim_tier_one_or_the_tier_two_catch_all(
        lookalike, why):
    """
    Adding a family-wide IIT/NIT catch-all to tier 2 created a second door
    a lookalike could walk through. never_match now bars both tiers.
    """
    assert institutions.classify(lookalike)["tier"] not in (
        "tier_1", "tier_2"), why


# ---------------------------------------------------------------------------
# Requirement 5: the same logic at every level, same maximum marks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("name,expected", [
    ("IIT Bombay", "tier_1"), ("NIT Warangal", "tier_1"),
    ("IIT Hyderabad", "tier_2"), ("NIT Durgapur", "tier_2"),
    ("University of Delhi", "tier_3"), ("Nowhere College", "unknown"),
])
def test_the_same_institution_gets_the_same_tier_at_every_level(
        level, name, expected):
    """
    Bachelor's, master's and doctorate must read one classification. A
    hierarchy applied at only one level would rank candidates by which
    degree happened to come from where.
    """
    factors = institution_factors(score_level(level, name))
    assert len(factors) == 1
    assert factors[0]["tier"] == expected


@pytest.mark.parametrize("level", LEVELS)
def test_the_tier_multiplier_reaching_the_score_is_the_configured_one(level):
    for name in ("IIT Bombay", "IIT Hyderabad", "University of Delhi"):
        verdict = institutions.classify(name)
        factor = institution_factors(score_level(level, name))[0]
        assert factor["value"] == verdict["multiplier"], \
            f"{level} applied a different multiplier than classify() returned"


def test_the_category_maximums_and_weights_are_untouched():
    """
    Requirement 5 again: the hierarchy redistributes credit WITHIN a
    qualification's marks, it does not change what those marks are.
    """
    assert scoring.WEIGHTS["doctorate"] == 15
    assert scoring.WEIGHTS["postdoctoral"] == 5
    assert scoring.WEIGHTS["masters"] == 8
    assert scoring.WEIGHTS["bachelors"] == 7
    assert sum(scoring.WEIGHTS.values()) == 100
    assert scoring.CATEGORY_TOTALS == {
        "research": 40, "experience": 25, "education": 35}


@pytest.mark.parametrize("level", LEVELS)
def test_a_tier_one_institution_earns_exactly_the_category_maximum(level):
    criterion = score_level(level, "IIT Bombay")
    assert criterion["points"] == pytest.approx(criterion["weight"])


# ---------------------------------------------------------------------------
# Requirement 6: once per qualification, inside the category limit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("name", ["IIT Bombay", "IIT Hyderabad",
                                  "University of Delhi", "Nowhere College"])
def test_institution_standing_is_applied_exactly_once(level, name):
    """
    The guard against a duplicate bonus: one institution factor per
    criterion, and the points are the product of the named factors and
    nothing else. A second, unnamed application would break the identity.
    """
    criterion = score_level(level, name)
    assert len(institution_factors(criterion)) == 1

    product = 1.0
    for factor in criterion["factors"]:
        product *= factor["value"]
    assert criterion["points"] == pytest.approx(
        criterion["weight"] * product), \
        "every awarded point must be traceable to a named factor"


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("name", ["IIT Bombay", "IIT Kharagpur",
                                  "IIT Hyderabad", "NIT Warangal",
                                  "University of Delhi", "IIT", "IIT Bombay"])
def test_no_institution_can_push_a_qualification_past_its_weight(level, name):
    criterion = score_level(level, name)
    assert 0.0 <= criterion["points"] <= criterion["weight"] + 1e-9


def test_the_education_category_cannot_exceed_its_total_at_any_tier():
    profile = {**profile_for("doctorate", "IIT Bombay"),
               **profile_for("masters", "IIT Bombay"),
               **profile_for("bachelors", "IIT Bombay"),
               "positions": []}
    result = scoring.score_candidate(
        {"candidate_id": "Synthetic_Test", "profile": profile,
         "validation_summary": {}},
        {"candidate_id": "Synthetic_Test", "publications": []},
        as_of=AS_OF, department="cse")
    education = result["category_scores"]["education"]
    assert education["earned"] <= education["available"] + 1e-9


def test_one_institution_named_across_all_three_degrees_is_not_compounded():
    """
    Three qualifications from one tier-1 institute earn three institution
    factors - one each - not a growing bonus. Each category is still
    capped at its own weight.
    """
    for level in LEVELS:
        criterion = score_level(level, "IIT Bombay")
        assert len(institution_factors(criterion)) == 1
        assert criterion["points"] == pytest.approx(criterion["weight"])


# ---------------------------------------------------------------------------
# Requirement 3: the classification is data, not code
# ---------------------------------------------------------------------------

def test_scoring_code_holds_no_institution_names():
    """
    scoring.py asks institutions.classify() and applies the number it
    returns. If a name appeared there, re-tiering would mean a code change
    and the configuration would no longer be the single source of truth.
    """
    names = ("kharagpur", "bombay", "madras", "kanpur", "roorkee",
             "guwahati", "warangal", "surathkal", "rourkela", "calicut",
             "iisc", "dhanbad", "varanasi", "pilani", "hyderabad")
    for module in ("scoring.py", "degrees.py", "explain.py", "schema.py"):
        text = (ROOT / "src" / module).read_text(encoding="utf-8").lower()
        for name in names:
            assert name not in text, \
                f"{module} names an institution ({name!r}); it belongs in config"


def test_every_tier_pattern_is_a_valid_regex_with_a_label():
    for tier_name, tier in institutions.config()["tiers"].items():
        for entry in tier["patterns"]:
            re.compile(entry["match"])
            assert entry.get("label"), \
                f"a pattern in {tier_name} has no label for the reviewer"
            if entry.get("needs_review"):
                assert entry.get("review_why"), \
                    "a flagged pattern must say what the reviewer should check"


def test_a_tier_can_be_changed_in_configuration_alone(monkeypatch):
    """
    The point of requirement 3, demonstrated: moving an institution
    between tiers is a data edit. Nothing in src/ changes, and the score
    follows.
    """
    before = score_level("doctorate", "IIT Kharagpur")
    assert before["points"] == pytest.approx(15.0)

    edited = copy.deepcopy(institutions.config())
    tier_1 = edited["tiers"]["tier_1"]["patterns"]
    moved = [p for p in tier_1 if "kharagpur" in p["match"]]
    assert moved, "fixture assumes a Kharagpur pattern exists in tier 1"
    edited["tiers"]["tier_1"]["patterns"] = [
        p for p in tier_1 if "kharagpur" not in p["match"]]
    edited["tiers"]["tier_2"]["patterns"] = (
        moved + edited["tiers"]["tier_2"]["patterns"])
    monkeypatch.setattr(institutions, "config", lambda: edited)

    after = score_level("doctorate", "IIT Kharagpur")
    assert institutions.classify("IIT Kharagpur")["tier"] == "tier_2"
    assert after["points"] == pytest.approx(12.75)


def test_the_tier_basis_is_published_to_an_administrator():
    """
    A committee that cannot read WHY the split falls where it does cannot
    approve or contest it.
    """
    rubric = institutions.visible_rubric()
    assert rubric["tier_basis"], "the basis for the split must be visible"
    basis = " ".join(rubric["tier_basis"]).lower()
    assert "founding" in basis
    assert rubric["version"] == institutions.config()["version"]
    for name in ("tier_1", "tier_2", "tier_3"):
        assert rubric["tiers"][name]["patterns"]
        assert rubric["multipliers"][name] == \
            institutions.config()["multipliers"][name]


def test_no_candidate_specific_logic_in_the_institution_configuration():
    """The generalisation guard, extended to the configuration file."""
    banned = ("candidate_0", "candidate0", "prabhat", "dansena", "apala",
              "banerjee", "vikas", "kumawat")
    text = (ROOT / "config" / "institutions.json").read_text(
        encoding="utf-8").lower()
    for token in banned:
        assert token not in text, f"the configuration references {token!r}"


# ---------------------------------------------------------------------------
# What the professor reads
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", LEVELS)
def test_a_flagged_campus_is_not_misreported_as_the_base_rate(level):
    """
    An IIT with no campus named is recognised and scored at the tier 2
    rate. The review note used to reuse the wording for an unknown
    college, telling the professor it "is not in the approved list" and
    "was scored at the base rate", which was false on both counts.
    """
    criterion = score_level(level, "Indian Institute of Technology")
    notes = " ".join(criterion["review_reasons"])
    assert "base rate" not in notes
    assert "not in the approved list" not in notes
    assert "0.85" in notes, "the note must state the rate actually applied"


@pytest.mark.parametrize("level", LEVELS)
def test_an_unknown_institution_is_still_reported_at_the_base_rate(level):
    notes = " ".join(score_level(level, "Nowhere College")["review_reasons"])
    assert "not in the approved list" in notes
    assert "base rate" in notes


@pytest.mark.parametrize("name", ["IIT Bombay", "IIT Hyderabad",
                                  "Indian Institute of Technology"])
def test_acronyms_survive_into_the_reviewer_text(name):
    """str.lower() and str.capitalize() both flattened IIT to 'iit'."""
    import explain
    criterion = score_level("doctorate", name)
    text = (explain.template_explanation(criterion) + " "
            + " ".join(criterion["review_reasons"]))
    assert "iit" not in text.replace("IIT", "")
    assert " nit" not in text.replace("NIT", "")
    assert ".." not in text, "a reason must not end in a doubled full stop"
