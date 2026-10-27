"""
PATENT TESTS. All fixtures synthetic - no real patent, person or employer.

Patents are a subcategory of the research criterion, so these tests check
three things that matter more than the arithmetic:
  * a patent must never score merely because the word appears;
  * a filing and the grant that follows it are ONE invention;
  * patents must not be able to outscore peer-reviewed work.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import schema    # noqa: E402
import scoring   # noqa: E402
import validate  # noqa: E402

CV = """
PATENTS
1. A Method for Thermal Regulation in Composite Panels.
   Application No. IN202411000123 A, filed 2024. Inventors: A Roy, B Sen.
2. System for Adaptive Mesh Refinement. Patent No. US11234567B2, granted 2023.
3. Apparatus for Acoustic Damping. Application No. IN202311009876, published 2023.
"""


def patent(title, number=None, status="unknown", evidence=None, **extra):
    return {"title": title, "number": number, "status": status,
            "evidence": evidence if evidence is not None else title,
            "inventors": [], **extra}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_a_patent_not_present_in_the_cv_is_excluded():
    source = validate.normalise(CV)
    results = validate.check_patents(
        [patent("A Wholly Invented Patent", "IN999", "granted")], source)
    assert "failed_validation" in results[0]["credit_exclusions"]


def test_a_patent_without_a_number_earns_nothing():
    """No number means no one can check the claim against any register."""
    source = validate.normalise(CV)
    results = validate.check_patents(
        [patent("A Method for Thermal Regulation in Composite Panels",
                None, "granted")], source)
    assert "no_identifier" in results[0]["credit_exclusions"]
    credit, rows = scoring.patent_credit(results)
    assert credit == 0.0


def test_a_filing_and_its_later_grant_count_once():
    """
    The common CV pattern: the same invention listed twice as it
    progressed. That is one contribution, not two.
    """
    source = validate.normalise(CV)
    results = validate.check_patents([
        patent("System for Adaptive Mesh Refinement", "US11234567B2", "filed"),
        patent("System for Adaptive Mesh Refinement", "US11234567B2", "granted"),
    ], source)
    excluded = [r for r in results if "duplicate_patent" in r["credit_exclusions"]]
    assert len(excluded) == 1
    # The surviving record must be the GRANTED one - the invention
    # progressed, it did not happen twice.
    kept = [r for r in results if not r["credit_exclusions"]]
    assert len(kept) == 1
    assert kept[0]["status_final"] == "granted"


def test_duplicates_are_caught_by_title_when_no_number_is_given():
    source = validate.normalise(CV)
    results = validate.check_patents([
        patent("Apparatus for Acoustic Damping", "IN202311009876", "published"),
        patent("Apparatus for Acoustic Damping", "IN202311009876", "filed"),
    ], source)
    assert sum(1 for r in results
               if "duplicate_patent" in r["credit_exclusions"]) == 1


def test_patents_are_never_marked_verified_against_a_register():
    """
    No patent register is consulted anywhere in this pipeline, and the
    record must say so rather than implying a check that did not happen.
    """
    source = validate.normalise(CV)
    results = validate.check_patents(
        [patent("System for Adaptive Mesh Refinement", "US11234567B2",
                "granted")], source)
    assert results[0]["verification"] == "unavailable"
    assert results[0]["verification_note"]


# ---------------------------------------------------------------------------
# Credit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    ("granted", 0.75),
    ("published", 0.25),
    ("filed", 0.0),
    ("unknown", 0.0),
])
def test_credit_follows_the_examination_stage(status, expected):
    credit, rows = scoring.patent_credit(
        [{"title": "X", "status_final": status, "credit_exclusions": []}])
    assert credit == expected


def test_an_unexamined_filing_earns_nothing():
    """
    A filed application has passed no examination. Crediting it would
    reward submitting a form.
    """
    credit, _ = scoring.patent_credit(
        [{"title": "X", "status_final": "filed", "credit_exclusions": []}])
    assert credit == 0.0


def test_the_word_patent_alone_earns_nothing():
    """Status 'unknown' means the CV named no stage."""
    credit, _ = scoring.patent_credit(
        [{"title": "Some Patent", "status_final": "unknown",
          "credit_exclusions": []}])
    assert credit == 0.0


# ---------------------------------------------------------------------------
# Interaction with publications
# ---------------------------------------------------------------------------

def _granted(n):
    return [{"title": f"Patent {i}", "status_final": "granted",
             "credit_exclusions": []} for i in range(n)]


def test_patents_cannot_outweigh_peer_reviewed_work():
    """
    Twenty granted patents and no papers must not saturate a criterion
    whose purpose is peer-reviewed scholarship.
    """
    criterion = scoring.score_publications([], _granted(20)).as_dict()
    ceiling = (scoring.FULL_MARKS_PUBLICATION_CREDIT
               * schema.PATENT_SHARE_OF_RESEARCH)
    assert criterion["patent_credit_counted"] == ceiling
    assert criterion["points"] < scoring.WEIGHTS["publications"]
    assert "capped" in criterion["note"]


def test_patents_supplement_publications_rather_than_replacing_them():
    with_patents = scoring.score_publications([], _granted(2)).as_dict()
    without = scoring.score_publications([], []).as_dict()
    assert with_patents["points"] > without["points"]
    assert with_patents["patent_credit_counted"] == 1.5      # 2 x 0.75


def test_a_candidate_with_no_patents_is_not_penalised():
    """
    Disciplines that do not patent must not carry an automatic zero.

    The property that matters is not the wording of the note - it is that
    a candidate with papers and no patents scores EXACTLY what they would
    have scored before patents existed in the rubric. Absence of patents
    must cost nothing.
    """
    papers = [{
        "title": "A Paper", "status": "published", "credit_exclusions": [],
        "verification": {"outcome": "matched", "cited_by_count": 0},
    }]
    with_feature = scoring.score_publications(papers, []).as_dict()
    # Same inputs, patents argument omitted entirely.
    without_feature = scoring.score_publications(papers).as_dict()

    assert with_feature["points"] == without_feature["points"]
    assert with_feature["patent_credit"] == 0.0
    # No zero-scored patent row is manufactured to sit in the evidence
    # table and read as a gap.
    assert not [row for row in with_feature["evidence"]
                if row.get("kind") == "patent"]


def test_the_breakdown_separates_papers_from_patents():
    """A reviewer must be able to see which kind of work earned what."""
    criterion = scoring.score_publications([], _granted(1)).as_dict()
    assert criterion["publication_credit"] == 0.0
    assert criterion["patent_credit"] == 0.75
    assert any(row.get("kind") == "patent" for row in criterion["evidence"])


def test_weights_still_sum_to_one_hundred():
    """Patents were folded in, not bolted on - the scale is unchanged."""
    assert sum(scoring.WEIGHTS.values()) == 100
