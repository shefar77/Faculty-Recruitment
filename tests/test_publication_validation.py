"""
Publication validation, DOI verification and deduplication.

WHAT WENT WRONG
    check_publication failed an entry whenever its evidence span was not
    found - including when the extractor never returned one, which it did
    for 33 of Candidate_08's 42 publications. Each of those titles was in
    the CV word for word, and 25 had been confirmed against OpenAlex with
    the candidate among the authors. All 33 were excluded as
    "failed_validation" and earned nothing; their citations were dropped
    from Research Visibility too. The written policy said the exclusion
    was for a title that could not be found. The code had drifted from it.

    The suite passed throughout because every fixture supplied an evidence
    span. These tests mostly do not.

ALL FIXTURES ARE SYNTHETIC except the Candidate_08 regression at the end,
which reads stored pipeline output and is skipped when it is absent. No
test touches the network: OpenAlex is stubbed throughout.

Run:  python -m pytest tests/test_publication_validation.py -v
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import schema       # noqa: E402
import scoring      # noqa: E402
import validate     # noqa: E402
import verify_pubs  # noqa: E402


CANDIDATE = "Asha Menon"


def work(title, authors, *, year=2023, cites=5, doi="https://doi.org/10.1/x"):
    return {
        "id": f"https://openalex.org/W{abs(hash(title)) % 10**8}",
        "display_name": title,
        "publication_year": year,
        "cited_by_count": cites,
        "doi": doi,
        "authorships": [{"author": {"display_name": a}, "raw_author_name": a}
                        for a in authors],
    }


@pytest.fixture
def index(monkeypatch):
    """
    A fake OpenAlex. Tests register works by title and by DOI, and can
    make either route fail as an outage.
    """
    state = {"by_title": {}, "by_doi": {}, "title_down": False,
             "doi_down": False, "doi_calls": 0}

    def search(title, use_cache=True):
        if state["title_down"]:
            return {}, "https://api.openalex.org/works?stub", "ConnectionError: down"
        hit = state["by_title"].get(verify_pubs.norm_title(title))
        return ({"results": [hit] if hit else []},
                "https://api.openalex.org/works?stub", "")

    def by_doi(doi, use_cache=True):
        state["doi_calls"] += 1
        url = f"https://api.openalex.org/works/doi:{doi}"
        if state["doi_down"]:
            return {}, url, "ReadTimeout: timed out"
        hit = state["by_doi"].get(doi)
        return (hit, url, "") if hit else ({}, url, "not in OpenAlex")

    monkeypatch.setattr(verify_pubs, "openalex_search", search)
    monkeypatch.setattr(verify_pubs, "openalex_by_doi", by_doi)
    monkeypatch.setattr(verify_pubs, "biblio_fallback",
                        lambda pub, name, use_cache=True: (None, None, "stub"))

    def add(w, *, doi=None):
        state["by_title"][verify_pubs.norm_title(w["display_name"])] = w
        if doi:
            state["by_doi"][doi] = w
        return w

    state["add"] = add
    return state


def run(cv: str, publications: list[dict], name: str = CANDIDATE):
    """Stages 4 -> 6 for publications only, exactly as run.py chains them."""
    record = {"candidate_id": "Synthetic",
              "profile": {"full_name": {"value": name, "evidence": name}},
              "publications": publications}
    validated = validate.validate_record(record, cv)
    verified = verify_pubs.verify_candidate(validated, use_cache=False)
    criterion = scoring.score_publications(verified["publications"])
    return validated, verified, criterion


def rows(criterion):
    return [r for r in criterion.evidence if r.get("kind") != "patent"]


# ---------------------------------------------------------------------------
# 1. The defect: a missing quotation is not a failed one
# ---------------------------------------------------------------------------

CV_JOURNALS = f"""{CANDIDATE}

Journal Articles
1 Menon, A., Rao, K., & Iyer, S. (2022). Pen Ink Discrimination in Handwritten
Documents using Texture Analysis. Multimedia Tools and Applications, 81(21),
30881-30909. https://doi.org/10.1007/s11042-022-12843-x
2 Rao, K., Menon, A. (2021). Generation of Synthetic Data for Word Alteration
Detection. IEEE Access, 9, 38979-38990.
"""


@pytest.mark.parametrize("evidence", [None, "", "   "])
def test_an_entry_with_no_quotation_is_not_failed_when_its_title_is_in_the_cv(
        index, evidence):
    index["add"](work("Generation of Synthetic Data for Word Alteration Detection",
                      ["K. Rao", "Asha Menon"], year=2021))
    validated, verified, criterion = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "venue": "IEEE Access", "year": 2021, "evidence": evidence}])

    assert validated["publications"][0]["validation"]["verdict"] == "verified"
    assert verified["publications"][0]["credit_exclusions"] == []
    assert rows(criterion)[0]["credit"] == 1.0


def test_the_flag_says_the_quotation_was_missing_not_that_it_was_wrong(index):
    validated, _, _ = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "evidence": None}])
    validation = validated["publications"][0]["validation"]
    assert validation["issues"] == []
    assert not any("not found in CV" in f for f in validation["flags"])
    assert any("returned no citation line" in f for f in validation["flags"])


def test_a_title_split_across_pdf_lines_is_still_found(index):
    """The CV breaks "Handwritten / Documents" across a line."""
    validated, _, _ = run(CV_JOURNALS, [{
        "title": "Pen Ink Discrimination in Handwritten Documents using "
                 "Texture Analysis", "evidence": None}])
    assert validated["publications"][0]["validation"]["verdict"] == "verified"


def test_a_hyphenated_line_break_inside_a_title_is_tolerated(index):
    cv = f"{CANDIDATE}\n1 Menon, A. (2020). Ink Analysis for Handwritten Document Foren-\nsics. IET Image Processing.\n"
    validated, _, _ = run(cv, [{
        "title": "Ink Analysis for Handwritten Document Forensics",
        "evidence": None}])
    assert validated["publications"][0]["validation"]["verdict"] == "verified"


def test_special_characters_in_a_title_are_tolerated(index):
    cv = (f"{CANDIDATE}\n7 Menon, A. (2025). A Zero-Reference Approach Employing "
          f"γ-Correction + Dilated-ZeroDCE++ for Cheque Image Enhancement.\n")
    validated, _, _ = run(cv, [{
        "title": "A Zero-Reference Approach Employing γ-Correction + "
                 "Dilated-ZeroDCE++ for Cheque Image Enhancement",
        "evidence": None}])
    assert validated["publications"][0]["validation"]["verdict"] == "verified"


# ---------------------------------------------------------------------------
# 2. The three Stage 4 outcomes stay distinct
# ---------------------------------------------------------------------------

def test_a_supplied_quotation_that_does_not_match_is_kept_and_flagged(index):
    """
    The title is in the CV, so the entry is real. The quotation is not,
    which may mean the extractor invented it - worth a human's eye, not
    worth deleting a confirmed paper over.
    """
    index["add"](work("Generation of Synthetic Data for Word Alteration Detection",
                      ["Asha Menon"]))
    validated, verified, criterion = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "evidence": "Published in Nature Machine Intelligence, vol 99, 2030"}])

    assert validated["publications"][0]["validation"]["verdict"] == "needs_review"
    assert verified["publications"][0]["credit_exclusions"] == []
    row = rows(criterion)[0]
    assert row["credit"] == 1.0
    assert "check this entry" in row["note"]


def test_a_title_absent_from_the_cv_is_still_excluded(index):
    """The one veto that remains, and the one the policy describes."""
    index["add"](work("A Paper The Candidate Never Listed", ["Asha Menon"]))
    validated, verified, criterion = run(CV_JOURNALS, [{
        "title": "A Paper The Candidate Never Listed", "evidence": None}])

    assert validated["publications"][0]["validation"]["verdict"] == "unverified"
    assert verified["publications"][0]["credit_exclusions"] == ["failed_validation"]
    assert rows(criterion)[0]["credit"] == 0.0


def test_an_entry_with_no_title_is_excluded(index):
    _, verified, _ = run(CV_JOURNALS, [{"title": "", "evidence": "IEEE Access"}])
    assert "failed_validation" in verified["publications"][0]["credit_exclusions"]


def test_an_exclusion_is_explained_in_plain_words(index):
    _, _, criterion = run(CV_JOURNALS, [{
        "title": "A Paper The Candidate Never Listed", "evidence": None}])
    reason = rows(criterion)[0]["reason"]
    assert reason == "not counted: its title could not be found in the CV"
    assert "_" not in reason


# ---------------------------------------------------------------------------
# 3. Statuses, and entries that cannot be checked
# ---------------------------------------------------------------------------

def test_an_accepted_paper_earns_what_the_matrix_says_and_no_more(index):
    cv = f"{CANDIDATE}\n1 Menon, A. (2026). Low-Light Action Recognition on IoT Devices. Internet Technology Letters. [Accepted]\n"
    validated, _, criterion = run(cv, [{
        "title": "Low-Light Action Recognition on IoT Devices",
        "status": "accepted", "evidence": "[Accepted]"}])
    assert validated["publications"][0]["status"] == "accepted"
    expected = schema.CREDIT_MATRIX[(schema.STATUS_CLASS["accepted"], "not_found")]
    assert rows(criterion)[0]["credit"] == expected


def test_an_entry_under_review_earns_nothing_even_when_indexed(index):
    """A preprint in the index is not a publication."""
    index["add"](work("A Preprint on Graph Learning", ["Asha Menon"]))
    cv = f"{CANDIDATE}\n1 Menon, A. A Preprint on Graph Learning. Under review.\n"
    _, _, criterion = run(cv, [{"title": "A Preprint on Graph Learning",
                                "status": "under review",
                                "evidence": "Under review."}])
    assert rows(criterion)[0]["credit"] == 0.0


def test_a_bare_title_with_no_metadata_earns_nothing_but_stays_visible(index):
    cv = f"{CANDIDATE}\nPublications\nA Study of Something Unindexed\n"
    validated, verified, criterion = run(cv, [{
        "title": "A Study of Something Unindexed", "evidence": None}])
    assert verified["publications"][0]["credit_exclusions"] == []
    row = rows(criterion)[0]
    assert row["credit"] == 0.0
    assert row["verification"] == "not_found"
    assert "not independently verifiable" in row["note"]


def test_an_index_outage_is_indeterminate_never_a_zero_finding(index):
    index["title_down"] = True
    _, verified, criterion = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "venue": "IEEE Access", "evidence": None}])
    assert verified["publications"][0]["verification"]["outcome"] == "lookup_failed"
    assert verified["publications"][0]["credit_exclusions"] == []
    assert "INDETERMINATE" in rows(criterion)[0]["reason"]
    assert criterion.indeterminate is True


# ---------------------------------------------------------------------------
# 4. DOIs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "https://doi.org/10.1109/ACCESS.2021.3059342",
    "http://dx.doi.org/10.1109/ACCESS.2021.3059342",
    "doi:10.1109/ACCESS.2021.3059342",
    "10.1109/access.2021.3059342",
    "https://doi.org/10.1109/ACCESS.2021.3059342.",
    "https : / / doi . org / 10 . 1109/ACCESS.2021.3059342",
])
def test_doi_spellings_all_normalise_to_one_key(raw):
    assert verify_pubs.normalise_doi(raw) == "10.1109/access.2021.3059342"


@pytest.mark.parametrize("raw", [
    None, "", "https://www.routledge.com/some-book/p/book/9781041006909",
    "not a doi",
])
def test_a_link_that_is_not_a_doi_is_not_treated_as_one(raw):
    assert verify_pubs.normalise_doi(raw) is None


def test_a_doi_identifies_a_paper_the_title_search_cannot(index):
    """The title search finds nothing; the DOI names the article exactly."""
    w = work("Generation of Synthetic Data for Word Alteration Detection",
             ["Dansena, P.", "Asha Menon"], cites=10)
    index["by_doi"]["10.1109/access.2021.3059342"] = w   # not by title
    _, verified, criterion = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "doi": "https://doi.org/10.1109/ACCESS.2021.3059342", "evidence": None}])

    v = verified["publications"][0]["verification"]
    assert v["resolved_by"] == "doi"
    assert v["outcome"] == "matched"
    assert rows(criterion)[0]["credit"] == 1.0


def test_a_doi_does_not_prove_authorship(index):
    """The DOI is real and the title matches - but she is not an author."""
    w = work("Generation of Synthetic Data for Word Alteration Detection",
             ["K. Rao", "S. Iyer"])
    index["by_doi"]["10.1109/access.2021.3059342"] = w
    _, verified, criterion = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "doi": "10.1109/ACCESS.2021.3059342", "evidence": None}])

    v = verified["publications"][0]["verification"]
    assert v["outcome"] == "unconfirmed_author"
    assert "NOT among its authors" in v["note"]
    assert rows(criterion)[0]["credit"] == 0.0


def test_a_doi_pointing_at_a_different_paper_is_ignored_not_trusted(index):
    """
    A DOI copied from the neighbouring entry. It must neither confirm this
    paper nor condemn it: the title search decides, and the mismatch is
    put in front of the reviewer.
    """
    index["by_doi"]["10.1016/j.physa.2019.124030"] = work(
        "Thermal Conductivity of CuO Nanofluids", ["Asha Menon"])
    index["add"](work("Generation of Synthetic Data for Word Alteration Detection",
                      ["Asha Menon"]))
    _, verified, criterion = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "doi": "https://doi.org/10.1016/j.physa.2019.124030", "evidence": None}])

    v = verified["publications"][0]["verification"]
    assert v.get("resolved_by") != "doi"
    assert v["outcome"] == "matched"          # by the title search
    assert "different article" in v["doi_note"]
    assert rows(criterion)[0]["credit"] == 1.0


def test_a_doi_lookup_failure_is_not_held_against_the_paper(index):
    index["doi_down"] = True
    index["add"](work("Generation of Synthetic Data for Word Alteration Detection",
                      ["Asha Menon"]))
    _, verified, criterion = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "doi": "10.1109/ACCESS.2021.3059342", "evidence": None}])

    v = verified["publications"][0]["verification"]
    assert v["outcome"] == "matched"
    assert "could not be looked up" in v["doi_note"]
    assert rows(criterion)[0]["credit"] == 1.0


def test_a_year_disagreement_is_a_weak_match_not_a_confirmation(index):
    """
    Title and author agree but the index dates the paper two years away
    from the CV. That is a probable match, not a confirmed one, and for an
    entry with no stated status the matrix gives it nothing.
    """
    index["add"](work("Generation of Synthetic Data for Word Alteration Detection",
                      ["Asha Menon"], year=2023))
    _, verified, criterion = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "year": 2021, "evidence": None}])
    assert verified["publications"][0]["verification"]["outcome"] == "weak_match"
    assert rows(criterion)[0]["credit"] == schema.CREDIT_MATRIX[("unknown", "weak_match")]


def test_a_doi_route_applies_the_same_year_rule_as_the_title_route(index):
    """
    The DOI route was first written with its own, looser test and would
    have confirmed a paper the title route calls a weak match. A DOI
    changes how a record is found, not what counts as a match.
    """
    w = work("Generation of Synthetic Data for Word Alteration Detection",
             ["Asha Menon"], year=2024)
    index["by_doi"]["10.1109/access.2021.3059342"] = w
    _, verified, _ = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "year": 2021, "doi": "10.1109/ACCESS.2021.3059342", "evidence": None}])
    v = verified["publications"][0]["verification"]
    assert v["resolved_by"] == "doi"
    assert v["outcome"] == "weak_match"
    assert "year differs" in v["note"]


@pytest.mark.parametrize("indexed_title, cv_year, oa_year, authors", [
    ("Generation of Synthetic Data for Word Alteration Detection", 2021, 2021, ["Asha Menon"]),
    ("Generation of Synthetic Data for Word Alteration Detection", 2021, 2025, ["Asha Menon"]),
    ("Generation of Synthetic Data for Word Alteration Detection", 2021, 2021, ["K. Rao"]),
    ("Generation of Synthetic Data for Handwritten Word Alteration", 2021, 2021, ["Asha Menon"]),
])
def test_both_routes_reach_the_same_outcome(monkeypatch, index,
                                            indexed_title, cv_year, oa_year, authors):
    pub = {"title": "Generation of Synthetic Data for Word Alteration Detection",
           "year": cv_year, "evidence": None}
    w = work(indexed_title, authors, year=oa_year)

    # Like the real index, return the work for a fuzzy query, not only an
    # exact one - otherwise the title route never sees it at all.
    monkeypatch.setattr(verify_pubs, "openalex_search", lambda t, use_cache=True: (
        {"results": [w]}, "https://api.openalex.org/works?stub", ""))
    by_title = verify_pubs.verify_publication(dict(pub), CANDIDATE, use_cache=False)

    monkeypatch.setattr(verify_pubs, "openalex_search", lambda t, use_cache=True: (
        {"results": []}, "https://api.openalex.org/works?stub", ""))
    index["by_doi"]["10.1109/access.2021.3059342"] = w
    by_doi = verify_pubs.verify_publication(
        dict(pub, doi="10.1109/ACCESS.2021.3059342"), CANDIDATE, use_cache=False)

    if by_doi.get("resolved_by") == "doi":
        assert by_doi["outcome"] == by_title["outcome"]


def test_no_doi_means_no_doi_lookup(index):
    run(CV_JOURNALS, [{"title": "Generation of Synthetic Data for Word "
                                "Alteration Detection", "evidence": None}])
    assert index["doi_calls"] == 0


# ---------------------------------------------------------------------------
# 5. Author names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("indexed", [
    "Asha Menon", "Menon, Asha", "A. Menon", "Menon, A.", "Dr. Asha Menon",
    "asha menon",
])
def test_the_candidate_is_found_however_the_index_writes_the_name(indexed):
    assert verify_pubs.names_match(CANDIDATE, indexed)


def test_a_colleague_with_the_same_surname_is_not_the_candidate():
    assert not verify_pubs.names_match(CANDIDATE, "Ravi Menon")


def test_the_candidate_is_found_among_many_authors(index):
    index["add"](work("A Systematic Multi-Criteria Framework for Hydrogen Production",
                      ["D. Yadav", "A. Dwivedi", "X. Lu", "C. B. Vishwakarma",
                       "D. Jing", "A. Menon", "R. R. Kumar"]))
    cv = (f"{CANDIDATE}\n3 Yadav, D., Dwivedi, A., Lu, X., Vishwakarma, C. B., "
          f"Jing, D., Menon, A., & Kumar, R. R. (2026). A Systematic "
          f"Multi-Criteria Framework for Hydrogen Production.\n")
    _, verified, _ = run(cv, [{
        "title": "A Systematic Multi-Criteria Framework for Hydrogen Production",
        "evidence": None}])
    assert verified["publications"][0]["verification"]["author_match"] == "A. Menon"


# ---------------------------------------------------------------------------
# 6. Duplicates
# ---------------------------------------------------------------------------

def test_the_same_doi_listed_twice_is_counted_once(index):
    index["add"](work("Generation of Synthetic Data for Word Alteration Detection",
                      ["Asha Menon"]))
    cv = CV_JOURNALS + ("\nSelected Publications\nGeneration of Synthetic Data "
                        "for Word Alteration Detection, IEEE Access 2021\n")
    pub = {"title": "Generation of Synthetic Data for Word Alteration Detection",
           "doi": "10.1109/ACCESS.2021.3059342", "evidence": None}
    _, verified, criterion = run(cv, [dict(pub), dict(pub)])

    excluded = [p["credit_exclusions"] for p in verified["publications"]]
    assert excluded.count(["duplicate_publication"]) == 1
    assert sum(r["credit"] for r in rows(criterion)) == 1.0


def test_the_better_verified_copy_is_the_one_credited(index):
    """
    The first listing has a truncated title the index cannot find; the
    second is complete and matched. Position must not decide.
    """
    index["add"](work("Pen Ink Discrimination in Handwritten Documents using "
                      "Texture Analysis", ["Asha Menon"]))
    cv = CV_JOURNALS + ("\nAlso listed\nPen Ink Discrimination in Handwritten "
                        "Documents using Texture Analysi\n")
    _, verified, _ = run(cv, [
        {"title": "Pen Ink Discrimination in Handwritten Documents using "
                  "Texture Analysi", "evidence": None},
        {"title": "Pen Ink Discrimination in Handwritten Documents using "
                  "Texture Analysis", "evidence": None},
    ])
    first, second = verified["publications"]
    assert first["credit_exclusions"] == ["duplicate_publication"]
    assert first["duplicate_of"] == 1
    assert second["credit_exclusions"] == []


def test_similar_but_different_papers_are_both_counted(index):
    """A sequel is not a duplicate."""
    a = "Low-Light Image Enhancement with Zero-Reference Curves"
    b = "Low-Light Cheque Image Enhancement with Text-Aware Curves"
    index["add"](work(a, ["Asha Menon"]))
    index["add"](work(b, ["Asha Menon"]))
    cv = f"{CANDIDATE}\n1 {a}.\n2 {b}.\n"
    _, verified, criterion = run(cv, [{"title": a, "evidence": None},
                                      {"title": b, "evidence": None}])
    assert all(p["credit_exclusions"] == [] for p in verified["publications"])
    assert sum(r["credit"] for r in rows(criterion)) == 2.0


def test_two_different_dois_are_never_merged_however_alike_the_titles(index):
    """
    Part I and Part II of a series score 0.91 against each other - just
    under the threshold. Distinct DOIs settle it regardless.
    """
    stem = ("Evaluation of ionization rates of charge carriers in a "
            "semiconductor via a generalized analytical model based on "
            "multistage scattering phenomena")
    a, b = f"{stem} - part I: Wurtzite-GaN", f"{stem} - part I: Wurtzite-GaAs"
    assert verify_pubs.title_similarity(a, b) >= schema.TITLE_SIMILARITY_EXACT
    index["add"](work(a, ["Asha Menon"]))
    index["add"](work(b, ["Asha Menon"]))
    cv = f"{CANDIDATE}\n1 {a}.\n2 {b}.\n"
    _, verified, _ = run(cv, [
        {"title": a, "doi": "10.1201/b20012-9", "evidence": None},
        {"title": b, "doi": "10.1201/b20012-10", "evidence": None}])
    assert all(p["credit_exclusions"] == [] for p in verified["publications"])


# ---------------------------------------------------------------------------
# 7. Author lists the index returns as one string
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("blob", [
    "A. Menon, K. Rao, S. Iyer & R. Das",
    "K. Rao, S. Iyer, A. Menon & R. Das",
    "R. Das, K. Rao, S. Iyer & A. Menon",
])
def test_a_whole_byline_in_one_author_field_matches_in_any_order(blob):
    """
    Two chapters of one book, same authors in a different order, came back
    one matched and one not. The order must not decide.
    """
    names = verify_pubs.split_author_blob(blob)
    assert len(names) == 4
    assert any(verify_pubs.names_match(CANDIDATE, n) for n in names)


@pytest.mark.parametrize("single", [
    "Menon, Asha", "Asha Menon", "Anand Kumar", "Menon, A.",
])
def test_a_single_name_is_not_split(single):
    assert verify_pubs.split_author_blob(single) == [single]


def test_a_byline_of_surname_initials_pairs_is_rejoined_correctly():
    assert verify_pubs.split_author_blob("Menon, A., Rao, K., Iyer, S.") == \
        ["Menon, A.", "Rao, K.", "Iyer, S."]


# ---------------------------------------------------------------------------
# 8. Status, read from the entry when there is no quotation
# ---------------------------------------------------------------------------

CV_STATUSES = f"""{CANDIDATE}

Conference Proceedings
9 Mahala, A., Menon, A., & Kumar, G. (2025). A Zero-Reference-Based Deep Enhance-
ment Approach for Low-Light Images. In Proceedings of ICMOCE-2025.
Accepted and Presented.
10 Menon, A., Rao, K. (2025). Protein Function Prediction with Attention.
In Proceedings of PEIS. Under review.
11 Menon, A. (2018). Thermal Conductivity of Nanofluids Using Neural Networks.
In Proceedings of INCOM-2018.
"""


def test_a_stated_status_survives_a_missing_quotation(index):
    """
    "Accepted and Presented." is printed in the entry. The extractor read
    it correctly but returned no quotation, and the guard downgraded it
    to unknown for want of one.
    """
    validated, _, _ = run(CV_STATUSES, [{
        "title": "A Zero-Reference-Based Deep Enhancement Approach for "
                 "Low-Light Images",
        "status": "accepted and presented", "evidence": None}])
    pub = validated["publications"][0]
    assert pub["status"] == "accepted"
    assert pub["validation"]["status_downgraded"] is None


def test_the_entry_below_cannot_lend_its_status(index):
    """
    Entry 11 states no status. Entry 10, above it, says "Under review" -
    reading forward from 11 must stop before anything else, and reading
    from 10 must not reach 11.
    """
    validated, _, _ = run(CV_STATUSES, [
        {"title": "Thermal Conductivity of Nanofluids Using Neural Networks",
         "status": "accepted", "evidence": None}])
    pub = validated["publications"][0]
    assert pub["status"] == "unknown"
    assert pub["validation"]["status_downgraded"]["reason"] == \
        "the CV entry does not state this status"


def test_the_found_word_must_state_the_same_status(index):
    """Some status word is not enough: it has to be the claimed one."""
    validated, _, _ = run(CV_STATUSES, [{
        "title": "Protein Function Prediction with Attention",
        "status": "published", "evidence": None}])
    assert validated["publications"][0]["status"] == "unknown"


def test_an_entry_is_found_when_a_line_break_splits_its_opening_words():
    cv = (f"{CANDIDATE}\n3 Yadav, D., Menon, A. (2026). A Sys-\ntematic "
          f"Multi-Criteria Framework for Hydrogen Production. IJGE. Accepted.\n")
    text = validate.entry_text(
        "A Systematic Multi-Criteria Framework for Hydrogen Production", cv)
    assert "Accepted" in text


@pytest.mark.parametrize("line, starts_entry", [
    ("10 Mahala, A., Ranjan, A.", True),
    ("[3] Menon, A. A Paper.", True),
    ("3. Menon, A. A Paper.", True),
    ("• Menon, A. A Paper.", True),
    ("30881-30909. Retrieved from https://doi.org/x", False),
    ("1016/j.cep.2024.110149", False),
    ("24(1), 20240027.", False),
])
def test_what_counts_as_the_start_of_the_next_entry(line, starts_entry):
    assert bool(validate.ENTRY_START.match(line)) is starts_entry


# ---------------------------------------------------------------------------
# 9. Visibility reads the same corrected decision
# ---------------------------------------------------------------------------

def test_citations_of_an_entry_with_no_quotation_are_counted(index):
    """
    Research Visibility skipped every excluded entry, so the same defect
    zeroed a candidate's citations as well as his papers.
    """
    index["add"](work("Generation of Synthetic Data for Word Alteration Detection",
                      ["Asha Menon"], cites=23))
    _, verified, _ = run(CV_JOURNALS, [{
        "title": "Generation of Synthetic Data for Word Alteration Detection",
        "evidence": None}])
    assert scoring.score_visibility(verified["publications"]).raw == 23


# ---------------------------------------------------------------------------
# 10. The real document
# ---------------------------------------------------------------------------

OUT = ROOT / "output"
STORED = [OUT / "Candidate_08.extracted.json", OUT / "Candidate_08.txt"]


@pytest.mark.skipif(not all(p.exists() for p in STORED),
                    reason="Candidate_08 pipeline output not present")
def test_candidate_08_no_traceable_publication_is_excluded():
    """
    Stage 4 only - no network. Every title the extractor returned is in
    the CV, so none may be refused for traceability, whatever happened to
    the quotations.
    """
    extracted = json.load(io.open(STORED[0], encoding="utf-8"))
    text = io.open(STORED[1], encoding="utf-8").read()
    validated = validate.validate_record(extracted, text)

    no_quote = [p for p in extracted["publications"]
                if not (p.get("evidence") or "").strip()]
    assert len(no_quote) > 20, "fixture no longer exercises the defect"

    verdicts = [p["validation"]["verdict"] for p in validated["publications"]]
    assert "unverified" not in verdicts
