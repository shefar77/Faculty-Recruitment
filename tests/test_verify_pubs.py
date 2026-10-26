"""
STAGE 5 TESTS - verdict logic and name matching, with no network access.

Every test here stubs the OpenAlex call, so the suite is deterministic and
runs offline. The live behaviour is exercised separately by running
src/verify_pubs.py, whose responses are cached on disk.

Run:  python -m pytest tests/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import schema      # noqa: E402
import verify_pubs  # noqa: E402


def fake_work(title, authors, year=2024):
    return {
        "id": "https://openalex.org/W999",
        "display_name": title,
        "publication_year": year,
        "cited_by_count": 7,
        "doi": "https://doi.org/10.0000/fake",
        "authorships": [
            {"author": {"display_name": a}, "raw_author_name": a} for a in authors
        ],
    }


def stub_search(monkeypatch, works):
    def _search(title, use_cache=True):
        return {"results": works}, "https://api.openalex.org/works?stubbed", ""
    monkeypatch.setattr(verify_pubs, "openalex_search", _search)


# ---------------------------------------------------------------------------
# Name matching - the formats that actually appear on CVs and in indexes
# ---------------------------------------------------------------------------

def test_name_variants_match():
    assert verify_pubs.names_match("Shiva Dixit", "Dr. Shiva Dixit")
    assert verify_pubs.names_match("Arpit Singh Yadav", "Yadav, Arpit Singh")
    assert verify_pubs.names_match("Arpit Singh Yadav", "A. S. Yadav")
    assert verify_pubs.names_match("Shiva Dixit", "S. Dixit")
    assert verify_pubs.names_match("Ayush Kumar", "ayush kumar")


def test_name_variants_reject_different_people():
    # Same surname, different person - the case that makes common names risky.
    assert not verify_pubs.names_match("Ayush Kumar", "Rajesh Kumar")
    assert not verify_pubs.names_match("Shiva Dixit", "Manish Dev Shrimali")
    # A bare surname is too weak to accept.
    assert not verify_pubs.names_match("Shiva Dixit", "Dixit")


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

def test_matched_requires_title_and_author_and_year(monkeypatch):
    stub_search(monkeypatch, [fake_work("Pathway selection by an active droplet",
                                        ["Shiva Dixit", "P. Parmananda"], 2023)])
    out = verify_pubs.verify_publication(
        {"title": "Pathway selection by an active droplet", "year": 2023},
        "Shiva Dixit")
    assert out["outcome"] == "matched"
    assert out["author_match"] is not None
    assert out["title_similarity"] >= schema.TITLE_SIMILARITY_EXACT
    assert out["query_url"]                      # evidence URL always present


def test_title_only_match_never_yields_matched(monkeypatch):
    """The core honesty rule: a title hit alone is not authorship."""
    stub_search(monkeypatch, [fake_work("Attention Is All You Need",
                                        ["Ashish Vaswani", "Noam Shazeer"], 2017)])
    out = verify_pubs.verify_publication(
        {"title": "Attention Is All You Need", "year": 2017}, "Amar Deep")
    assert out["outcome"] == "unconfirmed_author"
    assert out["author_match"] is None
    # Wording must not accuse the candidate of anything.
    assert "not a finding of non-authorship" in out["note"]


def test_year_mismatch_downgrades_to_weak(monkeypatch):
    stub_search(monkeypatch, [fake_work("Taming explosive growth through linear augmentation",
                                        ["Shiva Dixit"], 2019)])
    out = verify_pubs.verify_publication(
        {"title": "Taming explosive growth through linear augmentation", "year": 2024},
        "Shiva Dixit")
    assert out["outcome"] == "weak_match"


def test_year_within_tolerance_still_matches(monkeypatch):
    """Online-first vs issue year differ by one constantly."""
    stub_search(monkeypatch, [fake_work("Emergent rhythms in coupled nonlinear oscillators",
                                        ["Shiva Dixit"], 2024)])
    out = verify_pubs.verify_publication(
        {"title": "Emergent rhythms in coupled nonlinear oscillators", "year": 2025},
        "Shiva Dixit")
    assert out["outcome"] == "matched"


def test_unrelated_results_are_not_found(monkeypatch):
    stub_search(monkeypatch, [fake_work("Something Entirely Different",
                                        ["Someone Else"], 2024)])
    out = verify_pubs.verify_publication(
        {"title": "Principle of least action exhibited by active particles",
         "year": 2024}, "Shiva Dixit")
    assert out["outcome"] == "not_found"
    assert out["title_similarity"] < schema.TITLE_SIMILARITY_CLOSE


def test_empty_results_are_not_found(monkeypatch):
    stub_search(monkeypatch, [])
    out = verify_pubs.verify_publication({"title": "Skill-Sculptor", "year": 2025},
                                         "Ayush Kumar")
    assert out["outcome"] == "not_found"


def test_lookup_failure_is_not_zero(monkeypatch):
    """An outage must never look like an absent publication."""
    def _boom(title, use_cache=True):
        return {}, "https://api.openalex.org/works?x", "ConnectionError: refused"
    monkeypatch.setattr(verify_pubs, "openalex_search", _boom)

    out = verify_pubs.verify_publication({"title": "Anything", "year": 2024}, "A B")
    assert out["outcome"] == "lookup_failed"
    assert schema.CREDIT_MATRIX[("completed", "lookup_failed")] is schema.INDETERMINATE
    assert schema.CREDIT_MATRIX[("unknown", "lookup_failed")] is schema.INDETERMINATE


# ---------------------------------------------------------------------------
# Evidence completeness - rule 5
# ---------------------------------------------------------------------------

def test_every_verdict_carries_its_evidence(monkeypatch):
    stub_search(monkeypatch, [fake_work("A Paper", ["Jane Roe"], 2024)])
    out = verify_pubs.verify_publication({"title": "A Paper", "year": 2024}, "Jane Roe")
    for field in schema.VERIFICATION_EVIDENCE_FIELDS:
        assert field in out, f"missing required evidence field: {field}"


def test_thesis_entry_is_marked_for_credit_exclusion(monkeypatch):
    """
    A dissertation must score zero even if OpenAlex indexes it.

    Institutional repositories do index theses, so without the exclusion
    this entry would reach ("unknown", "matched") = 1.00 - full credit for
    work the degree already accounts for.
    """
    stub_search(monkeypatch, [fake_work(
        "Predicting Earthquake Magnitude & Depth Using BiGRU", ["Amar Deep"], 2025)])

    validated = {
        "candidate_id": "Candidate_01",
        "profile": {"full_name": {"value": "Amar Deep", "evidence": "Amar Deep"}},
        "publications": [{
            "title": "Predicting Earthquake Magnitude & Depth Using BiGRU",
            "year": 2025,
            "status": "unknown",
            "validation": {
                "verdict": "unverified",
                "flags": ["evidence mentions a thesis/dissertation - may not be "
                          "a publication (reviewer judgement)"],
            },
        }],
    }
    result = verify_pubs.verify_candidate(validated)
    entry = result["publications"][0]

    # Even though OpenAlex "found" it with the author confirmed...
    assert entry["verification"]["outcome"] == "matched"
    # ...both exclusions are recorded, and they override the matrix.
    assert "thesis_or_dissertation" in entry["credit_exclusions"]
    assert "failed_validation" in entry["credit_exclusions"]
    for exclusion in schema.CREDIT_EXCLUSIONS:
        if exclusion["id"] in entry["credit_exclusions"]:
            assert exclusion["credit"] == 0.00
            assert exclusion["overrides_matrix"] is True


# ---------------------------------------------------------------------------
# Bibliographic fallback - titles that changed before publication
# ---------------------------------------------------------------------------

def test_parse_biblio_handles_real_citation_shapes():
    assert verify_pubs.parse_biblio(
        "Physical Chemistry Chemical Physics, 26, 25590 (2024).") == ("26", "25590")
    assert verify_pubs.parse_biblio(
        "European Physical Letters, 148 (2), 23002 (2024).") == ("148", "23002")
    assert verify_pubs.parse_biblio(
        "Chaos, Solitons & Fractals, 181, 114639, (2024).") == ("181", "114639")
    # Volume but no page.
    assert verify_pubs.parse_biblio(
        "Chaos: An Interdisciplinary Journal, 35 (12), (2025).") == ("35", None)


def _pccp_work():
    """The real record: same journal/volume/page/authors, different title."""
    return {
        "id": "https://openalex.org/W4401234567",
        "display_name": "Surface tension gradient invoked path selection",
        "publication_year": 2024,
        "cited_by_count": 3,
        "doi": "https://doi.org/10.1039/d4cp01832j",
        "biblio": {"volume": "26", "first_page": "25590"},
        "primary_location": {
            "source": {"display_name": "Physical Chemistry Chemical Physics"}},
        "authorships": [
            {"author": {"display_name": n}, "raw_author_name": n}
            for n in ["Anjuman Ara Khatun", "Aarsh Chotalia", "Kalpita Das",
                      "Shiva Dixit", "P. Parmananda"]
        ],
    }


PCCP_PUB = {
    "title": "Principle of least action exhibited by active particles",
    "year": 2024,
    "venue": "Physical Chemistry Chemical Physics",
    "evidence": "Physical Chemistry Chemical Physics, 26, 25590 (2024).",
    "status": "unknown",
}


def test_renamed_paper_is_recovered_by_biblio_fallback(monkeypatch):
    """A title search finding nothing must not be read as 'does not exist'."""
    stub_search(monkeypatch, [])          # title search returns nothing

    def _fallback(pub, candidate_name, use_cache=True):
        return _pccp_work(), "https://api.openalex.org/works?fallback", ""
    monkeypatch.setattr(verify_pubs, "biblio_fallback", _fallback)

    out = verify_pubs.verify_publication(PCCP_PUB, "Dr. Shiva Dixit")
    assert out["outcome"] == "matched_by_biblio"
    assert out["doi"] == "https://doi.org/10.1039/d4cp01832j"
    assert out["matched_first_page"] == "25590"
    assert out["author_match"] == "Shiva Dixit"
    # The reviewer must be told the titles differ rather than it passing silently.
    assert "TITLE DIFFERS" in out["note"]


def test_biblio_match_earns_the_same_credit_as_a_title_match():
    for status_class in ("completed", "unknown"):
        assert (schema.CREDIT_MATRIX[(status_class, "matched_by_biblio")]
                == schema.CREDIT_MATRIX[(status_class, "matched")])
    # ...but never rescues work that has not cleared peer review.
    assert schema.CREDIT_MATRIX[("in_progress", "matched_by_biblio")] == 0.00


def test_biblio_fallback_rejects_a_different_article(monkeypatch, tmp_path):
    """Same author and year, wrong page: must NOT match."""
    wrong = _pccp_work()
    wrong["biblio"] = {"volume": "26", "first_page": "11111"}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [wrong]}

    monkeypatch.setattr(verify_pubs, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(verify_pubs, "_cache_path",
                        lambda url: tmp_path / "probe.json")
    monkeypatch.setattr(verify_pubs.requests, "get",
                        lambda *a, **k: _Response())

    work, _, why = verify_pubs.biblio_fallback(PCCP_PUB, "Dr. Shiva Dixit")
    assert work is None
    assert "no record matched" in why


def test_biblio_fallback_needs_a_year():
    """Unpublished work has no year, so the fallback must decline cleanly."""
    pub = dict(PCCP_PUB, year=None)
    work, _, why = verify_pubs.biblio_fallback(pub, "Dr. Shiva Dixit")
    assert work is None
    assert "no author/year" in why


def test_total_outage_routes_every_entry_to_lookup_failed(monkeypatch):
    """
    Not one lookup failing - the whole service unreachable for a full run.
    Every entry must come back lookup_failed with its query URL, nothing
    skipped, and no exception escaping the stage.
    """
    def unreachable(*args, **kwargs):
        raise verify_pubs.requests.exceptions.ConnectionError("unreachable")
    monkeypatch.setattr(verify_pubs.requests, "get", unreachable)

    validated_record = {
        "candidate_id": "C",
        "profile": {"full_name": {"value": "Shiva Dixit", "evidence": "x"}},
        "publications": [
            {"title": f"Paper {i}", "year": 2024, "status": "published",
             "venue": "A Journal", "evidence": "A Journal, 1, 1 (2024).",
             "validation": {"verdict": "verified", "flags": []}}
            for i in range(19)
        ],
    }
    result = verify_pubs.verify_candidate(validated_record, use_cache=False)

    assert len(result["publications"]) == 19, "entries were skipped"
    for entry in result["publications"]:
        verification = entry["verification"]
        assert verification["outcome"] == "lookup_failed"
        assert verification["query_url"], "evidence URL missing"
        assert "INDETERMINATE" in verification["note"]
