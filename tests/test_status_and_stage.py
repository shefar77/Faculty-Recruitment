"""
Publication status, bibliographic completeness, and the submitted thesis.

Three defects found by reading one real CV closely, pinned here with
SYNTHETIC fixtures so the fixes are about the rule and never about that
resume:

  1. An unrecognised status word fell through to "unknown" - the one
     status class with a full-credit promotion path.
  2. A fully cited, published paper scored zero for lacking the word
     "Published", while the same entry with that word scored 0.25.
  3. "Ph.D. (Thesis Submitted)" had no slot in the vocabulary and scored
     the same as a first-semester enrolment.

And one presentation defect: the explanation blamed every zero on the
index, so papers the candidate had labelled "under review" read as papers
the system had failed to find.

Run:  python -m pytest tests/test_status_and_stage.py -v
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import explain    # noqa: E402
import institutions  # noqa: E402
import schema     # noqa: E402
import scoring    # noqa: E402
import validate   # noqa: E402

AS_OF = date(2026, 10, 15)


def wrap(value, evidence="evidence text"):
    return {"value": value, "evidence": evidence}


def pub(title, status, outcome, venue=""):
    return {"title": title, "status": status, "venue": venue,
            "credit_exclusions": [],
            "verification": {"outcome": outcome, "cited_by_count": 0,
                             "openalex_id": "https://openalex.org/W1",
                             "query_url": "https://api.openalex.org/works?x"}}


def publications(result):
    return next(c for c in result["criteria"] if c["criterion"] == "publications")


def doctorate(result):
    return next(c for c in result["criteria"] if c["criterion"] == "doctorate")


def score(profile=None, pubs=None):
    profile = dict(profile or {})
    profile.setdefault("positions", [])
    return scoring.score_candidate(
        {"candidate_id": "Synthetic", "profile": profile,
         "validation_summary": {}},
        {"candidate_id": "Synthetic", "publications": pubs or []},
        as_of=AS_OF)


# ---------------------------------------------------------------------------
# 1. An unrecognised status must not inherit the full-credit path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("communicated", "submitted"),
    ("Communicated", "submitted"),
    ("under communication", "submitted"),
    ("under review", "under_revision"),
    ("Major revision submitted", "under_revision"),
    ("in press", "in_press"),
    ("published", "published"),
    ("under_revision", "under_revision"),
    (None, "unknown"),
    ("", "unknown"),
])
def test_status_words_are_normalised_through_the_surface_forms(raw, expected):
    assert schema.normalise_status(raw)[0] == expected


def test_an_unrecognised_status_word_is_reported_not_absorbed():
    status, recognised = schema.normalise_status("pending decision xyz")
    assert status == "unknown"
    assert recognised is False


def test_a_communicated_paper_found_in_the_index_earns_nothing():
    """
    The hole itself. "Communicated" means submitted to a journal. If its
    preprint is indexed, the old code read the raw word as "unknown" and
    paid FULL credit through the promotion path - for work that has
    passed no review at all.
    """
    result = score(pubs=[pub("A Preprint", "communicated", "matched")])
    assert publications(result)["points"] == 0.0


def test_scoring_normalises_even_if_validation_was_skipped():
    """Defence in depth: the credit line must not trust its caller."""
    for word in ("communicated", "under review", "submitted"):
        result = score(pubs=[pub("A Preprint", word, "matched")])
        assert publications(result)["points"] == 0.0, word


def test_validation_flags_a_status_it_cannot_read():
    checked = validate.check_publication(
        0, {"title": "A Paper", "status": "pending decision xyz",
            "evidence": "A Paper, pending decision xyz"},
        validate.normalise("A Paper, pending decision xyz"))
    assert checked["status_recognised"] is False
    assert any("does not recognise" in f for f in checked["flags"])


# ---------------------------------------------------------------------------
# 2. A complete citation is a publication claim
# ---------------------------------------------------------------------------

def _checked(evidence, status="unknown", venue=""):
    entry = {"title": "A Paper Title", "status": status,
             "evidence": evidence, "venue": venue}
    return validate.check_publication(
        0, entry, validate.normalise(evidence))


@pytest.mark.parametrize("evidence", [
    "A Paper Title, Annual Technical Volume, Institution of Engineers, "
    "2017, pp. 57-60",
    "A Paper Title, in Proceedings of a Conference, Springer, 2025, "
    "pp. 415-427",
    "A Paper Title, Journal of Things, vol. 12, 2023",
    "A Paper Title, Journal of Things, 2023, doi 10.1234/abcd.5678",
])
def test_a_venue_plus_a_locator_reads_as_published(evidence):
    checked = _checked(evidence)
    assert checked["status_final"] == "published"
    assert checked["status_from_citation"]["to"] == "published"


def test_a_bare_title_stays_unknown():
    """The omission loophole stays closed: silence is not publication."""
    assert _checked("A Paper Title")["status_final"] == "unknown"


def test_a_venue_without_a_locator_stays_unknown():
    """'Journal of Things' alone could be an intention, not a fact."""
    assert _checked("A Paper Title, Journal of Things, 2023")[
        "status_final"] == "unknown"


def test_a_complete_citation_never_overrides_a_declared_review_state():
    """
    The disqualifier is the state of peer review, not our ability to
    identify the venue. A manuscript number and page count do not make
    a paper under review into a published one.
    """
    checked = _checked(
        "A Paper Title, Journal of Things, 2025, pp. 1-20 [Under Review]",
        status="under_revision")
    assert checked["status_final"] == "under_revision"
    assert checked["status_from_citation"] is None


def test_an_unindexed_published_paper_now_earns_the_partial_credit():
    """
    The arithmetic consequence: a complete citation outside the index
    lands on (completed, not_found) = 0.25, the same as the identical
    entry with the word "Published" typed beside it.
    """
    result = score(pubs=[pub("A Paper", "published", "not_found")])
    assert publications(result)["measured"] == 0.25


# ---------------------------------------------------------------------------
# 3. A submitted thesis is its own stage - partial, flagged, not eligible
# ---------------------------------------------------------------------------

def _rows(status, evidence, year=None, degree="phd"):
    return [
        {"field": "phd_status", "value": status, "evidence": evidence,
         "status": "verified"},
        {"field": "phd_year", "value": year, "evidence": evidence,
         "status": "verified"},
        {"field": "highest_degree", "value": degree, "evidence": evidence,
         "status": "verified"},
    ]


def _by(rows):
    return {r["field"]: r for r in rows}


@pytest.mark.parametrize("evidence", [
    "Ph.D. (Thesis Submitted), Department of X  2021 - 2026",
    "PhD - thesis under evaluation, 2020 - 2025",
    "Doctor of Philosophy, awaiting viva, 2019 - 2024",
    "Ph.D., thesis has been submitted (2022)",
])
def test_a_stated_submission_moves_pursuing_to_thesis_submitted(evidence):
    rows = _rows("pursuing", evidence)
    validate.cross_check_thesis_submitted(rows)
    assert _by(rows)["phd_status"]["value"] == "thesis_submitted"
    assert _by(rows)["phd_status"]["corrected_from"] == "pursuing"
    assert _by(rows)["highest_degree"]["value"] == "phd_submitted"


def test_an_awarded_claim_on_a_submitted_row_is_downgraded():
    """
    The direction that matters most. A model reading "Thesis Submitted
    2021 - 2026" as awarded in 2026 has manufactured a qualification.
    """
    rows = _rows("awarded", "Ph.D. (Thesis Submitted)  2021 - 2026", year=2026)
    validate.cross_check_thesis_submitted(rows)
    assert _by(rows)["phd_status"]["value"] == "thesis_submitted"
    assert _by(rows)["phd_year"]["value"] is None, \
        "the end of an enrolment range is not an award year"


def test_pre_submission_does_not_count_as_submitted():
    """A pre-submission seminar happens BEFORE the thesis goes in."""
    rows = _rows("pursuing", "Ph.D., pre-submission seminar completed 2025")
    validate.cross_check_thesis_submitted(rows)
    assert _by(rows)["phd_status"]["value"] == "pursuing"


def test_only_the_doctorate_rows_own_evidence_is_read():
    """
    "Thesis submitted" elsewhere in a CV - about a supervised student, in
    a referee's note - must not be credited to the candidate.
    """
    rows = _rows("pursuing", "Ph.D., Department of X, 2022 - Present")
    validate.cross_check_thesis_submitted(rows)
    assert _by(rows)["phd_status"]["value"] == "pursuing"


def test_an_untraceable_row_is_never_corrected():
    """
    A span the validator could not find in the CV is unproven text.
    Reading a stage out of it would launder a hallucination.
    """
    rows = _rows("pursuing", "Ph.D. (Thesis Submitted)")
    rows[0]["status"] = "unverified"
    validate.cross_check_thesis_submitted(rows)
    assert _by(rows)["phd_status"]["value"] == "pursuing"


def test_a_submitted_thesis_earns_part_of_the_doctorate_marks():
    result = score({"phd_status": wrap("thesis_submitted"),
                    "phd_institution": wrap("IIT Bombay")})
    entry = doctorate(result)
    expected = (scoring.WEIGHTS["doctorate"]
                * schema.DOCTORATE_STAGE_CREDIT["thesis_submitted"])
    assert entry["points"] == pytest.approx(expected)
    assert 0 < entry["points"] < scoring.WEIGHTS["doctorate"]
    assert entry["needs_review"] is True


def test_a_submitted_thesis_scores_above_an_enrolment_and_below_an_award():
    base = {"phd_institution": wrap("IIT Bombay")}
    pursuing = doctorate(score({**base, "phd_status": wrap("pursuing")}))
    submitted = doctorate(score({**base, "phd_status": wrap("thesis_submitted")}))
    awarded = doctorate(score({**base, "phd_status": wrap("awarded"),
                               "phd_year": wrap(2024)}))
    assert pursuing["points"] < submitted["points"] < awarded["points"]


def test_a_submitted_thesis_never_satisfies_the_eligibility_gate():
    """The gate decides appointability; an unconferred award is not one."""
    result = score({"phd_status": wrap("thesis_submitted"),
                    "phd_institution": wrap("IIT Bombay")})
    assert result["eligibility"]["outcome"] == "indeterminate"
    assert "not yet a PhD holder" in result["eligibility"]["reason"]


def test_net_still_satisfies_the_gate_alongside_a_submitted_thesis():
    result = score({"phd_status": wrap("thesis_submitted"),
                    "net_qualified": wrap(True)})
    assert result["eligibility"]["outcome"] == "met"


# ---------------------------------------------------------------------------
# 4. The explanation must attribute every zero to its real cause
# ---------------------------------------------------------------------------

def _explained(pubs):
    return explain.template_explanation(publications(score(pubs=pubs)))


def test_papers_under_review_are_not_described_as_missing_from_the_index():
    prose = _explained([
        pub("A", "published", "matched"),
        pub("B", "under_revision", "not_found"),
        pub("C", "submitted", "not_found"),
    ])
    assert "2 still under review or submitted" in prose
    assert "not a penalty" in prose
    assert "could not be confirmed" not in prose


def test_a_partially_credited_paper_is_not_called_corroborated():
    prose = _explained([
        pub("A", "published", "matched"),
        pub("B", "published", "not_found"),
    ])
    assert "1 found in the OpenAlex index" in prose
    assert "1 given as a complete citation but not found" in prose


def test_the_explanation_states_the_full_marks_threshold():
    prose = _explained([pub("A", "published", "matched")])
    assert f"{scoring.FULL_MARKS_PUBLICATION_CREDIT:g} publication-equivalents" \
        in prose


def test_the_thesis_stage_is_stated_in_the_doctorate_explanation():
    prose = explain.template_explanation(doctorate(score({
        "phd_status": wrap("thesis_submitted"),
        "phd_institution": wrap("IIT Bombay")})))
    assert "thesis submitted, not yet awarded" in prose
    assert "0.5" in prose


# ---------------------------------------------------------------------------
# A near-collision this CV surfaced: a college named after the same family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "B.K. Birla Institute of Engineering & Technology, Pilani",
    "Birla Institute of Engineering and Technology",
])
def test_a_similarly_named_college_does_not_claim_bits_pilani_tier(name):
    assert institutions.classify(name)["tier"] != "tier_1"
