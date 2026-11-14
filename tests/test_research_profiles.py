"""
Research profiles and research scoring: ORCID retrieval, duplicate
prevention, verification status, provisional credit, and the h-index.

Network calls are replaced with an in-memory fake; nothing here touches
OpenAlex. Names and identifiers are synthetic.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import research_profiles as rp   # noqa: E402
import scoring                   # noqa: E402
import validate                  # noqa: E402
import verify_pubs               # noqa: E402

AS_OF = date(2026, 11, 14)
ORCID = "0000-0002-1825-0097"        # ORCID's own published example iD


def work(title, year=2023, cites=10, kind="article", venue_type="journal",
         doi=None, wid=None):
    return {"id": wid or f"https://openalex.org/W{abs(hash(title)) % 10**8}",
            "doi": doi, "display_name": title, "type": kind,
            "publication_year": year, "cited_by_count": cites,
            "primary_location": {"source": {"display_name": "Some Journal",
                                            "type": venue_type}}}


@pytest.fixture
def openalex(monkeypatch):
    state = {"author": {"display_name": "Asha Menon", "works_count": 5,
                        "cited_by_count": 240,
                        "summary_stats": {"h_index": 8, "i10_index": 6}},
             "works": [], "down": False}

    def fake(url, *, use_cache=True, missing_ok=False):
        if state["down"]:
            return {}, "ConnectionError: unreachable"
        if "/authors/" in url:
            return dict(state["author"]), ""
        return {"results": list(state["works"])}, ""

    monkeypatch.setattr(verify_pubs, "cached_get_json", fake)
    return state


def cv_row(title, outcome="matched", openalex_id=None, doi=None):
    return {"title": title, "credit_exclusions": [],
            "verification": {"outcome": outcome, "openalex_id": openalex_id,
                             "doi": doi, "matched_title": title}}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_orcid_check_digit_is_enforced():
    assert rp.orcid_checksum_ok(ORCID)
    assert not rp.orcid_checksum_ok("0000-0002-1825-0098")


def test_links_and_claims_are_detected():
    links = rp.detect_links(
        f"ORCID: {ORCID}\nhttps://scholar.google.com/citations?user=AbCdEfGhIjKl\n"
        "Scopus ID: 57200000000\n"
        "Research output: 25+ peer-reviewed journal publications; "
        "500+ Google Scholar citations; h-index 12\n"
        "Selected Publications\n")
    assert links["orcid"] == [ORCID]
    assert links["scholar"][0]["user"] == "AbCdEfGhIjKl"
    assert links["scopus"][0]["id"] == "57200000000"
    assert links["claimed_publications"] == 25
    assert links["self_reported"] == {"h_index": 12, "citations": 500}
    assert links["partial_list"] is True


def test_a_phone_number_is_not_an_orcid():
    assert rp.detect_links("Phone: 1234-5678-9012-3456")["orcid"] == []


# ---------------------------------------------------------------------------
# Retrieval and identity
# ---------------------------------------------------------------------------

def test_a_matching_profile_is_verified(openalex):
    profile = rp.fetch_orcid_profile(ORCID, "Dr. Asha Menon")
    assert profile["status"] == "verified"
    assert profile["h_index"] == 8
    assert profile["query_urls"]


def test_a_profile_in_someone_elses_name_is_not_used(openalex):
    openalex["author"]["display_name"] = "Rahul Verma"
    profile = rp.fetch_orcid_profile(ORCID, "Asha Menon")
    assert profile["status"] == "name_mismatch"
    assert rp.profile_works(profile, [], {"partial_list": True}) == []


def test_an_outage_is_recorded_not_held_against_the_candidate(openalex):
    openalex["down"] = True
    profile = rp.fetch_orcid_profile(ORCID, "Asha Menon")
    assert profile["status"] == "lookup_failed"
    assert "not held against" in profile["note"]


# ---------------------------------------------------------------------------
# Profile-only works: duplicates, types, the CV's own claim
# ---------------------------------------------------------------------------

def profile_with(openalex, works):
    openalex["works"] = works
    return rp.fetch_orcid_profile(ORCID, "Asha Menon")


def test_works_already_in_the_cv_are_not_counted_twice(openalex):
    listed = work("A Study of Graphs", doi="https://doi.org/10.1/abc", wid="W1")
    profile = profile_with(openalex, [listed, work("Another Result", wid="W2")])
    rows = rp.profile_works(profile,
                            [cv_row("A study of graphs.", openalex_id="Wx",
                                    doi="10.1/abc")],
                            {"partial_list": True})
    assert [r["title"] for r in rows] == ["Another Result"]


def test_preprints_and_datasets_earn_nothing(openalex):
    profile = profile_with(openalex, [
        work("A Preprint", kind="preprint"),
        work("A Repository Copy", venue_type="repository"),
        work("A Dataset", kind="dataset")])
    rows = rp.profile_works(profile, [], {"partial_list": True})
    assert all(r["credit"] == 0 for r in rows)


def test_profile_works_count_only_when_the_cv_says_its_list_is_partial(openalex):
    profile = profile_with(openalex, [work("Unlisted Paper")])
    complete = rp.profile_works(profile, [], {"partial_list": False})
    partial = rp.profile_works(profile, [], {"partial_list": True,
                                             "partial_list_evidence": "Selected Publications"})
    assert complete[0]["credit"] == 0 and "not credited" in complete[0]["reason"]
    assert partial[0]["credit"] == rp.PROFILE_WORK_CREDIT


def test_profile_credit_stops_at_the_number_the_cv_claims(openalex):
    profile = profile_with(openalex, [work(f"Paper {i}", cites=i) for i in range(10)])
    cv = [cv_row(f"Listed {i}") for i in range(3)]
    rows = rp.profile_works(profile, cv, {"partial_list": True,
                                          "claimed_publications": 5})
    assert sum(1 for r in rows if r["credit"]) == 2


def test_two_index_records_of_one_work_count_once(openalex):
    profile = profile_with(openalex, [work("Same Title Here", wid="W1"),
                                      work("Same title here.", wid="W2")])
    rows = rp.profile_works(profile, [], {"partial_list": True})
    assert sum(1 for r in rows if r["credit"]) == 1


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def pub(title, status="published", outcome="matched", cites=0):
    return {"title": title, "status": status, "credit_exclusions": [],
            "verification": {"outcome": outcome, "cited_by_count": cites,
                             "openalex_id": f"W-{title}"}}


def score(verified: dict) -> dict:
    validated = {"profile": {}, "validation_summary": {}, "patents": []}
    result = scoring.score_candidate(validated, verified, as_of=AS_OF)
    return {c["criterion"]: c for c in result["criteria"]} | {"_": result}


def test_an_outage_keeps_the_cvs_own_claim_as_provisional_credit():
    crit = score({"publications": [pub("A", outcome="lookup_failed")]})
    assert crit["publications"]["evidence"][0]["credit"] == \
        scoring.schema.CREDIT_MATRIX[("completed", "not_found")] > 0
    assert crit["publications"]["indeterminate"] is True
    assert crit["_"]["scoring_reliable"] is False


def test_profile_works_add_to_research_output():
    base = score({"publications": [pub("A")]})["publications"]["points"]
    extra = score({"publications": [pub("A")],
                   "profile_works": [{"title": "B", "credit": 1.0,
                                      "cited_by_count": 4}]})
    assert extra["publications"]["points"] > base
    assert extra["publications"]["profile_credit"] == 1.0


def test_the_verified_h_index_can_carry_visibility():
    profiles = [{"status": "verified", "id": ORCID, "h_index": 10,
                 "cited_by_count": 50}]
    crit = score({"publications": [pub("A", cites=5)],
                  "research_profiles": profiles})
    assert crit["research_visibility"]["points"] == scoring.WEIGHTS["research_visibility"]


def test_self_reported_metrics_are_flagged_not_scored():
    crit = score({"publications": [pub("A", cites=20)],
                  "research_links": {"self_reported": {"h_index": 15,
                                                       "citations": 900}}})
    vis = crit["research_visibility"]
    assert vis["measured"] == 20
    assert vis["needs_review"]
    assert "not scored" in vis["review_reasons"][0]


def test_an_award_without_a_year_is_credited_and_flagged():
    profile = {"phd_status": {"value": "awarded", "evidence": "Ph.D. Physics"},
               "phd_institution": {"value": None}}
    crit = scoring.score_doctorate(profile, {})
    assert crit.points > 0
    assert crit.needs_review
    gate = scoring.evaluate_gate(profile, {})
    assert gate["outcome"] == "indeterminate"


# ---------------------------------------------------------------------------
# Patents and roles
# ---------------------------------------------------------------------------

def test_an_application_forms_empty_patent_row_is_dropped():
    rows = validate.check_patents(
        [{"title": "NA", "number": None, "evidence": "1   NA   NA   NA"},
         {"title": "A Real Device", "number": "IN123456",
          "evidence": "A Real Device IN123456"}],
        validate.normalise("A Real Device IN123456"))
    assert [r["title"] for r in rows] == ["A Real Device"]


@pytest.mark.parametrize("title,organisation,category", [
    ("Research Scientist", "Example Labs Private Limited", "industry"),
    ("Postdoctoral Researcher", "Example Labs Private Limited", "research"),
    ("Research Scientist", "Example Institute of Technology", "research"),
])
def test_a_research_title_at_a_company_is_industry(title, organisation, category):
    from roles import classify_position
    assert classify_position({"title": title,
                              "organisation": organisation})["category"] == category
