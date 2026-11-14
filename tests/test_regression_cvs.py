"""
Regression cases from real CVs whose qualifications were lost or misread.

Each fixture is the CV text Stage 1 produced and the model output Stage 3
returned, stored together so these tests replay Stages 4 and 6 offline and
deterministically. The model output is deliberately the FLAWED one that
caused the original errors - "pursuing" for a finished doctorate, a missing
master's, "masters" in place of a degree name - so each test proves the
pipeline now recovers from it.

The expectations are facts printed in each CV, not scores tuned to pass.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import scoring    # noqa: E402
import validate   # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "regression"
AS_OF = date(2026, 11, 14)


def validated(stem: str) -> dict:
    record = json.loads((FIXTURES / f"{stem}.extracted.json").read_text(encoding="utf-8"))
    text = (FIXTURES / f"{stem}.txt").read_text(encoding="utf-8")
    return validate.validate_record(record, text, as_of=AS_OF)


def value(result: dict, field: str):
    return (result["profile"].get(field) or {}).get("value")


def education(result: dict) -> dict:
    profile = result["profile"]
    return {
        "doctorate": scoring.score_doctorate(profile, {}),
        "masters": scoring._score_degree(profile, "masters", "", "cse"),
        "bachelors": scoring._score_degree(profile, "bachelors", "", "cse"),
        "gate": scoring.evaluate_gate(profile, result["validation_summary"]),
    }


# ---------------------------------------------------------------------------
# Every qualification the CV prints is found, with its completion year
# ---------------------------------------------------------------------------

CASES = {
    # stem: (phd year, masters name, masters year, bachelors name, bachelors year)
    "Candidate_02": (2023, "M.Sc", 2018, "B.Sc.", 2016),       # table, Year of Passing
    "Candidate_03": (2020, None, 2014, None, 2009),             # side-heading layout
    "Candidate_04": (2022, "M.Sc", 2013, "B.Sc.", 2011),        # table, LLM evidence not verbatim
    "Candidate_05": (2026, None, 2019, "B.E.", 2015),           # "Jan 2022 - Feb 2026"
    "Candidate_06": (2024, "Master of Science", 2018,
                     "Bachelor of Science", 2016),              # prose, "masters" placeholder
}


@pytest.mark.parametrize("stem", sorted(CASES))
def test_completed_doctorate_is_recognised(stem):
    result = validated(stem)
    assert value(result, "phd_status") == "awarded"
    assert value(result, "phd_year") == CASES[stem][0]
    assert not result["validation_summary"]["doctorate_cross_check_failed"]
    marks = education(result)
    assert marks["doctorate"].points > 0
    assert marks["gate"]["outcome"] == "met"


@pytest.mark.parametrize("stem", sorted(CASES))
def test_masters_and_bachelors_are_credited(stem):
    _, m_name, m_year, b_name, b_year = CASES[stem]
    result = validated(stem)
    if m_name:
        assert value(result, "masters_degree") == m_name
    if b_name:
        assert value(result, "bachelors_degree") == b_name
    assert value(result, "masters_year") == m_year
    assert value(result, "bachelors_year") == b_year
    marks = education(result)
    assert marks["masters"].points > 0
    assert marks["bachelors"].points > 0


def test_science_degrees_score_below_engineering_degrees_but_above_zero():
    """B.Sc / M.Sc earn credit at the approved lower ratio, never zero."""
    sci = education(validated("Candidate_06"))
    eng = education(validated("Candidate_05"))
    sci_ratio = next(f["value"] for f in sci["bachelors"].factors
                     if f["factor"] == "equivalence")
    eng_ratio = next(f["value"] for f in eng["bachelors"].factors
                     if f["factor"] == "equivalence")
    assert 0 < sci_ratio < eng_ratio == 1.0


def test_inferred_completion_records_its_basis_and_corroboration():
    status = validated("Candidate_06")["profile"]["phd_status"]
    assert status["source"] == "cv_rule_parser"
    assert "2024" in status["completion_basis"]
    assert any("thesis" in c for c in status["corroboration"])
    assert any("postdoctoral" in c for c in status["corroboration"])
    assert status["needs_confirmation"] is False


def test_a_passing_year_is_not_read_as_a_start_year():
    """The model read 'Year of Passing 2023' as the PhD's start year."""
    result = validated("Candidate_02")
    assert value(result, "phd_start_year") is None
    assert value(result, "phd_year") == 2023


# ---------------------------------------------------------------------------
# Publication titles split across table columns are traced to the CV
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stem", ["Candidate_02", "Candidate_04"])
def test_table_publications_are_traced_to_the_cv(stem):
    verdicts = [p["verdict"] for p in validated(stem)["publication_detail"]]
    # Candidate_02 lists one entry the extractor truncated beyond
    # recognition; every other entry must be traceable.
    assert verdicts.count("unverified") <= 1


def test_a_ligature_damaged_title_is_traced():
    """'Di!erences' in the PDF text is 'Differences' in the title."""
    detail = validated("Candidate_04")["publication_detail"]
    hit = next(p for p in detail if "Various Coloring" in p["title"])
    assert hit["verdict"] != "unverified"


def test_an_na_patent_row_is_not_a_patent():
    assert validated("Candidate_04")["patents"] == []


def test_a_research_post_at_a_private_company_is_industry():
    positions = validated("Candidate_04")["profile"]["positions"]
    folium = next(p for p in positions
                  if "Folium" in (p.get("organisation") or ""))
    assert folium["category"] == "industry"


# ---------------------------------------------------------------------------
# Research profiles are detected, not guessed
# ---------------------------------------------------------------------------

def test_profile_links_and_self_reported_metrics_are_detected():
    links = validated("Candidate_06")["research_links"]
    assert links["orcid"] == ["0000-0002-3921-2639"]
    assert links["partial_list"] is True
    assert links["claimed_publications"] == 20
    assert links["self_reported"]["h_index"] == 13
    assert links["self_reported"]["citations"] == 400

    links = validated("Candidate_05")["research_links"]
    assert links["orcid"] == ["0009-0003-5776-5194"]
    assert links["scholar"] and links["scopus"]
    assert links["partial_list"] is False


# ---------------------------------------------------------------------------
# Candidates that were already right stay right
# ---------------------------------------------------------------------------

def test_a_submitted_thesis_is_still_not_an_award():
    result = validated("Candidate_07")
    assert value(result, "phd_status") == "thesis_submitted"
    assert value(result, "phd_year") is None
    assert education(result)["gate"]["outcome"] == "indeterminate"


@pytest.mark.parametrize("stem,year", [("Candidate_08", 2022),
                                       ("Candidate_09", 2024),
                                       ("Candidate_10", 2020)])
def test_previously_correct_doctorates_are_unchanged(stem, year):
    result = validated(stem)
    assert value(result, "phd_status") == "awarded"
    assert value(result, "phd_year") == year


def test_a_previously_missed_msc_is_now_found():
    result = validated("Candidate_10")
    assert value(result, "masters_degree") == "MSc."
    assert value(result, "masters_year") == 2014
