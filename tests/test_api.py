"""
STAGE 8 TESTS - the HTTP layer.

No LLM calls and no network: these exercise the translation between HTTP
and the pipeline functions, against a temporary database. The pipeline's
own behaviour is already covered by the Stage 1-7 suites, and the point of
this layer is that it adds no behaviour of its own.

Run:  python -m pytest tests/ -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import store                   # noqa: E402
from src.api.main import app   # noqa: E402

CRITERION = {
    "criterion": "teaching_experience",
    "weight": 25, "measured": 2.17, "unit": "years",
    "fraction_of_weight": 0.434, "points": 10.85,
    "source_fields": ["positions[].category"], "evidence": [],
    "insufficient_evidence": False, "note": "", "unclassified_roles": [],
}


def _audit(candidate_id, score, eligibility, name="Test Person"):
    return {
        "candidate_id": candidate_id,
        "source": {"file": f"{candidate_id}.pdf", "sha256": "abc"},
        "verification": {"publications": [{
            "title": "A Paper", "status": "published",
            "credit_exclusions": [],
            "verification": {"outcome": "matched", "title_similarity": 1.0,
                             "author_match": "Test Person",
                             "openalex_id": "https://openalex.org/W1",
                             "query_url": "https://api.openalex.org/works?x",
                             "cited_by_count": 5, "note": "ok"},
        }]},
        "scoring": {
            "candidate_name": name, "total_score": score, "max_score": 100,
            "eligibility": {"outcome": eligibility, "reason": "because",
                            "requirement": "doctorate or NET"},
            "rubric": {"version": "demo-rubric-v1", "disclaimer": "DEMO"},
            "as_of_date": "2026-10-15", "scored_at": "2026-10-15T00:00:00+00:00",
            "criteria": [dict(CRITERION)],
            "reviewer_flags": [],
        },
        "explanations": {"criterion_explanations": [
            {"criterion": "teaching_experience", "explanation": "Scored.",
             "source": "template"}],
            "eligibility_explanation": "Eligibility: " + eligibility},
        "disclaimer": "DEMO/RESEARCH rubric.",
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client backed by a throwaway database."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "api.db")
    connection = store.connect()
    store.save_audit(connection, _audit("Cand_A", 80.0, "met", "Alice"))
    # Close enough to Cand_A that a criterion override (capped at the
    # 25-point weight) can actually overtake it - otherwise a "does the
    # ranking move?" test cannot move anything.
    store.save_audit(connection, _audit("Cand_B", 70.0, "met", "Bob"))
    store.save_audit(connection, _audit("Cand_C", 90.0, "indeterminate", "Cleo"))
    connection.close()
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_listing_separates_eligible_from_gated(client):
    body = client.get("/candidates").json()
    assert [c["candidate_id"] for c in body["eligible"]] == ["Cand_A", "Cand_B"]
    assert [c["candidate_id"] for c in body["gated"]] == ["Cand_C"]
    assert body["counts"] == {"eligible": 2, "gated": 1, "total": 3}


def test_a_high_scoring_gated_candidate_never_enters_the_eligible_group(client):
    """Cand_C scores 90 - above both eligible candidates - and still must
    not appear among them."""
    body = client.get("/candidates").json()
    assert "Cand_C" not in [c["candidate_id"] for c in body["eligible"]]
    assert body["gated"][0]["computed_total"] == 90.0


def test_listing_always_returns_both_computed_and_effective(client):
    body = client.get("/candidates").json()
    for candidate in body["eligible"] + body["gated"]:
        assert "computed_total" in candidate
        assert "effective_total" in candidate
        assert "computed_eligibility" in candidate
        assert "effective_eligibility" in candidate


# ---------------------------------------------------------------------------
# On-demand explanations
#
# Prose moved off the upload path because it was 27% of the runtime and the
# one output that cannot change a result. These tests pin down that it is
# genuinely separable: the endpoint writes sentences and nothing else.
# ---------------------------------------------------------------------------

def test_detail_reports_whether_prose_has_been_generated(client):
    body = client.get("/candidates/Cand_A").json()
    state = body["explanation_state"]
    assert state["criteria_total"] == 1
    assert state["llm_written"] == 0        # fixture stores templates
    assert state["all_llm"] is False


def test_explain_endpoint_writes_prose_without_touching_any_score(
        client, monkeypatch):
    import explain

    def fake(scored, *, use_llm=True):
        return {"candidate_id": scored.get("candidate_id"),
                "criterion_explanations": [
                    {"criterion": "teaching_experience",
                     "explanation": "Scored 10.85 of 25 from 2.17 years.",
                     "source": "llm"}],
                "eligibility_explanation": "gate text",
                "llm_used": True, "explanations_rejected": 0}

    monkeypatch.setattr(explain, "explain_candidate", fake)

    before = client.get("/candidates/Cand_A").json()
    response = client.post("/candidates/Cand_A/explain")
    assert response.status_code == 200
    body = response.json()
    assert body["criteria_updated"] == 1
    assert body["llm_used"] is True
    assert body["state"]["all_llm"] is True

    after = client.get("/candidates/Cand_A").json()
    # The entire numeric surface must be untouched.
    assert after["computed_total"] == before["computed_total"]
    assert after["effective_total"] == before["effective_total"]
    assert after["computed_eligibility"] == before["computed_eligibility"]
    for old, new in zip(before["criteria"], after["criteria"]):
        assert new["computed_points"] == old["computed_points"]
        assert new["effective_points"] == old["effective_points"]
        assert new["measured"] == old["measured"]
        assert new["note"] == old["note"]
    # ...and only the prose changed.
    assert after["criteria"][0]["explanation"] != before["criteria"][0]["explanation"]
    assert after["criteria"][0]["explanation_source"] == "llm"


def test_explain_endpoint_404s_for_an_unknown_candidate(client):
    assert client.post("/candidates/Nobody/explain").status_code == 404


def test_explain_endpoint_reports_a_model_failure_as_503(client, monkeypatch):
    """
    An unreachable model must not look like a problem with the candidate.
    """
    import explain

    def boom(scored, *, use_llm=True):
        raise ConnectionError("ollama unreachable")

    monkeypatch.setattr(explain, "explain_candidate", boom)
    response = client.post("/candidates/Cand_A/explain")
    assert response.status_code == 503
    assert "ollama unreachable" in response.json()["detail"]


def test_a_failed_explanation_leaves_the_stored_prose_intact(
        client, monkeypatch):
    import explain

    before = client.get("/candidates/Cand_A").json()
    monkeypatch.setattr(explain, "explain_candidate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    client.post("/candidates/Cand_A/explain")
    after = client.get("/candidates/Cand_A").json()
    assert after["criteria"][0]["explanation"] == \
        before["criteria"][0]["explanation"]


def test_detail_returns_evidence(client):
    body = client.get("/candidates/Cand_A").json()
    assert body["candidate_name"] == "Alice"
    assert body["criteria"][0]["computed_points"] == 10.85
    publication = body["publications"][0]
    assert publication["openalex_id"].startswith("https://openalex.org/")
    assert publication["query_url"]
    assert "reviewer_flags" in body and "eligibility" in body


# ---------------------------------------------------------------------------
# The evidence has to survive the trip to the browser.
#
# Regression guard for a measured defect: the scorer recorded the roles,
# papers and uncategorised positions behind every number, and the UI knew
# how to render all three - but effective_state() rebuilds each criterion
# from the criteria TABLE, which has no column for any of them, so the API
# served a score with no evidence attached and the reviewer saw a bare
# figure. Both halves worked in isolation and the seam between them did
# not, which is exactly the kind of gap a component test cannot see.
# ---------------------------------------------------------------------------

EVIDENCE_AUDIT_CRITERION = {
    **CRITERION,
    "evidence": [{
        "title": "Assistant Professor", "organisation": "A University",
        "kind": "appointment", "from": "2024-10", "to": "present",
        "months": 24,
    }],
    "unscored_positions": [{
        "title": "Summer Intern", "organisation": "Veltrix",
        "from": "2022-07", "to": "2022-08",
        "reason": "employer name carries no academic or commercial marker",
    }],
    "indeterminate": True,
    "indeterminate_entries": 3,
}


@pytest.fixture
def evidenced(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "evidence.db")
    audit = _audit("Cand_E", 50.0, "met", "Eve")
    audit["scoring"]["criteria"] = [dict(EVIDENCE_AUDIT_CRITERION)]
    connection = store.connect()
    store.save_audit(connection, audit)
    connection.close()
    with TestClient(app) as test_client:
        yield test_client


def test_detail_carries_the_roles_behind_a_score(evidenced):
    criterion = evidenced.get("/candidates/Cand_E").json()["criteria"][0]
    assert criterion["evidence"], "the roles were dropped between store and API"
    role = criterion["evidence"][0]
    assert role["title"] == "Assistant Professor"
    assert role["organisation"] == "A University"
    assert role["months"] == 24


def test_detail_carries_roles_that_could_not_be_counted(evidenced):
    """A gap in our reading must reach the reviewer, not vanish into a zero."""
    criterion = evidenced.get("/candidates/Cand_E").json()["criteria"][0]
    unscored = criterion["unscored_positions"]
    assert [role["title"] for role in unscored] == ["Summer Intern"]
    assert unscored[0]["reason"]


def test_detail_carries_the_not_verified_state(evidenced):
    criterion = evidenced.get("/candidates/Cand_E").json()["criteria"][0]
    assert criterion["indeterminate"] is True
    assert criterion["indeterminate_entries"] == 3


def test_the_evidence_merge_cannot_change_a_score(evidenced):
    """
    The audit record is a display source here, never an authority on a
    number. If the two ever disagree, the stored score must win: a merge
    that could rewrite points would make the audit trail unfalsifiable.
    """
    connection = store.connect()
    stored = connection.execute(
        "SELECT audit_json FROM candidates WHERE candidate_id=?",
        ("Cand_E",)).fetchone()
    audit = json.loads(stored["audit_json"])
    audit["scoring"]["criteria"][0].update(
        {"points": 999.0, "weight": 999, "measured": 999.0})
    connection.execute(
        "UPDATE candidates SET audit_json=? WHERE candidate_id=?",
        (json.dumps(audit), "Cand_E"))
    connection.commit()
    connection.close()

    criterion = evidenced.get("/candidates/Cand_E").json()["criteria"][0]
    assert criterion["computed_points"] == 10.85
    assert criterion["effective_points"] == 10.85
    assert criterion["weight"] == 25


def test_detail_404(client):
    assert client.get("/candidates/Nobody").status_code == 404


def test_audit_endpoint_returns_every_stage(client):
    body = client.get("/candidates/Cand_A/audit").json()
    for stage in ("scoring", "verification", "explanations"):
        assert stage in body


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

def test_override_applies_and_preserves_the_computed_value(client):
    response = client.patch("/candidates/Cand_B/score", json={
        "field": "teaching_experience", "new_value": 20.85,
        "reason": "Two further years confirmed at interview.",
        "overridden_by": "Prof. Reviewer"})
    assert response.status_code == 200
    body = response.json()
    assert body["computed_total"] == 70.0        # immutable
    assert body["effective_total"] == 80.0       # +10.00 delta applied
    assert body["changed"]["total"] == [70.0, 80.0]


def test_override_moves_the_ranking(client):
    before = [c["candidate_id"] for c in client.get("/candidates").json()["eligible"]]
    assert before == ["Cand_A", "Cand_B"]

    # 10.85 -> 25.0 is +14.15, taking Cand_B from 70.0 to 84.15, past
    # Cand_A's 80.0.
    client.patch("/candidates/Cand_B/score", json={
        "field": "teaching_experience", "new_value": 25.0,
        "reason": "Full teaching record supplied after the deadline."})

    after = [c["candidate_id"] for c in client.get("/candidates").json()["eligible"]]
    assert after == ["Cand_B", "Cand_A"], "the override must reorder the list"


def test_eligibility_override_moves_a_candidate_between_groups(client):
    client.patch("/candidates/Cand_C/score", json={
        "field": "eligibility", "new_value": "met",
        "reason": "Award letter sighted; degree conferred."})

    body = client.get("/candidates").json()
    assert body["counts"] == {"eligible": 3, "gated": 0, "total": 3}
    promoted = next(c for c in body["eligible"] if c["candidate_id"] == "Cand_C")
    assert promoted["computed_eligibility"] == "indeterminate"   # preserved
    assert promoted["effective_eligibility"] == "met"            # applied
    assert promoted["candidate_id"] == body["eligible"][0]["candidate_id"]


@pytest.mark.parametrize("payload,status,fragment", [
    ({"field": "teaching_experience", "new_value": 5, "reason": "   "},
     422, "requires a reason"),
    ({"field": "charisma", "new_value": 5, "reason": "x"},
     422, "unknown field"),
    ({"field": "teaching_experience", "new_value": 999, "reason": "x"},
     422, "between 0 and its weight"),
    ({"field": "teaching_experience", "new_value": -1, "reason": "x"},
     422, "between 0 and its weight"),
    ({"field": "eligibility", "new_value": "maybe", "reason": "x"},
     422, "met, not_met or indeterminate"),
    ({"field": "teaching_experience", "new_value": "lots", "reason": "x"},
     422, "must be numeric"),
])
def test_invalid_overrides_are_refused(client, payload, status, fragment):
    response = client.patch("/candidates/Cand_A/score", json=payload)
    assert response.status_code == status
    assert fragment in str(response.json()["detail"])


def test_override_on_unknown_candidate_is_404(client):
    response = client.patch("/candidates/Ghost/score", json={
        "field": "teaching_experience", "new_value": 5, "reason": "x"})
    assert response.status_code == 404


def test_override_log_is_append_only_over_http(client):
    for value, reason in ((20.0, "First correction."), (15.0, "Revised.")):
        client.patch("/candidates/Cand_A/score", json={
            "field": "teaching_experience", "new_value": value,
            "reason": reason})

    log = client.get("/candidates/Cand_A/overrides").json()["overrides"]
    assert len(log) == 2                      # nothing overwritten
    detail = client.get("/candidates/Cand_A").json()
    assert detail["criteria"][0]["effective_points"] == 15.0   # latest wins
    assert detail["computed_total"] == 80.0                    # still immutable


# ---------------------------------------------------------------------------
# Upload validation (no pipeline run)
# ---------------------------------------------------------------------------

def test_upload_rejects_unsupported_file_type(client):
    response = client.post(
        "/candidates", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 400
    assert "unsupported file type" in response.json()["detail"]


def test_upload_rejects_a_path_traversal_filename(client):
    response = client.post(
        "/candidates",
        files={"file": ("../../etc/passwd.pdf", b"%PDF-", "application/pdf")})
    assert response.status_code in (400, 422)


def test_upload_requires_a_file(client):
    assert client.post("/candidates").status_code == 422


# ---------------------------------------------------------------------------
# Failure paths. A pipeline that cannot run must SAY SO, never quietly
# record a candidate as unqualified.
# ---------------------------------------------------------------------------

import extract_fields  # noqa: E402
import run as runner   # noqa: E402


def test_unreachable_model_aborts_instead_of_scoring_zero(client, monkeypatch):
    """
    The worst failure mode found in Stage 8 review. With Ollama down,
    extraction returns an empty-but-valid profile, scoring produces
    0.0/100 and the gate returns 'not_met' - identical to a genuinely
    unqualified applicant, with nothing saying the model was never
    reached.
    """
    def dead(prompt, num_ctx=None):
        return extract_fields.CallResult(
            ok=False, error="ConnectionError: refused", called_at="now")
    monkeypatch.setattr(extract_fields, "call_ollama", dead)

    pdf = ROOT / "data" / "cvs" / "Candidate_01.pdf"
    if not pdf.is_file():
        pytest.skip("sample CV not present")

    response = client.post(
        "/candidates?use_llm=false",
        files={"file": ("Candidate_01.pdf", pdf.read_bytes(),
                        "application/pdf")})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "extraction failed, refusing to score" in detail

    # And nothing was recorded.
    listed = client.get("/candidates").json()
    ids = [c["candidate_id"] for c in listed["eligible"] + listed["gated"]]
    assert "Candidate_01" not in ids


def test_extraction_failures_reports_each_failed_call():
    record = {"provenance": {
        "profile_call": {"ok": False, "error": "timeout"},
        "eligibility_call": {"ok": True, "error": ""},
        "publications_call": {"ok": False, "error": "refused"},
    }}
    failures = extract_fields.extraction_failures(record)
    assert len(failures) == 2
    assert any("profile_call" in f for f in failures)
    assert any("publications_call" in f for f in failures)
    assert not extract_fields.extraction_failures(
        {"provenance": {"a": {"ok": True}}})


def test_malformed_pdf_returns_422(client):
    response = client.post("/candidates?use_llm=false", files={
        "file": ("broken.pdf", b"this is not a pdf at all", "application/pdf")})
    assert response.status_code == 422
    assert "text extraction failed" in response.json()["detail"]


def test_pdf_without_a_text_layer_is_refused(tmp_path, client):
    """
    A scanned CV is an ordinary thing to receive. It must be refused with
    an explanation, not scored 0.0/100 as if the candidate were weak.
    """
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    document.new_page()                        # a page with no text at all
    path = tmp_path / "scanned.pdf"
    document.save(path)
    document.close()

    response = client.post("/candidates?use_llm=false", files={
        "file": ("scanned.pdf", path.read_bytes(), "application/pdf")})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "no usable text layer" in detail
    assert "never read" in detail              # says why it matters

    listed = client.get("/candidates").json()
    ids = [c["candidate_id"] for c in listed["eligible"] + listed["gated"]]
    assert "scanned" not in ids


def test_openalex_outage_does_not_zero_a_candidate(monkeypatch):
    """
    Contrast with the two above: an OpenAlex outage is NOT fatal. It
    yields 'lookup_failed', which the credit matrix maps to INDETERMINATE
    rather than 0, so an outage cannot silently erase a publication record.
    """
    import schema
    import verify_pubs

    def unreachable(title, use_cache=True):
        return {}, "https://api.openalex.org/works?x", "ConnectionError"
    monkeypatch.setattr(verify_pubs, "openalex_search", unreachable)

    outcome = verify_pubs.verify_publication(
        {"title": "A Paper", "year": 2024}, "Jane Roe")
    assert outcome["outcome"] == "lookup_failed"
    assert schema.CREDIT_MATRIX[("completed", "lookup_failed")] is schema.INDETERMINATE
