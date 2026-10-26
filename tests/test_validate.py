"""
STAGE 4 TEST - prove the validator rejects invented data.

The real model's mistakes on the four sample CVs were mostly misreadings
(an enum leaking into a text field, a compressed quote). Those are useful,
but they do not prove the validator stops OUTRIGHT FABRICATION - a plausible
institution, a plausible paper, a plausible co-author, none of which exist
in the CV. So this test fabricates exactly that and asserts it is caught.

Run:  python -m pytest tests/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import validate  # noqa: E402

# A short, entirely real CV to validate against.
SOURCE_CV = """Amar Deep
Lakhimpur Kheri, Uttar Pradesh
amardeepg0706@gmail.com

Education
Sardar Vallabhbhai National Institute of Technology (NIT) Surat   Aug 2023 - July 2025
Master of Technology, Computer Science and Engineering            CGPA: 6.63

Experience
Assistant Professor - Mangalmay Group of Institutes, Greater Noida   Aug 2025 - Present
"""


def _wrap(value, evidence):
    return {"value": value, "evidence": evidence}


def build_fabricated_record() -> dict:
    """Honest fields mixed with invented ones, as a real failure would look."""
    return {
        "candidate_id": "Candidate_TEST",
        "profile": {
            # --- true, and present in the CV -----------------------------
            "full_name": _wrap("Amar Deep", "Amar Deep"),
            "email": _wrap("amardeepg0706@gmail.com", "amardeepg0706@gmail.com"),
            "masters_institution": _wrap(
                "Sardar Vallabhbhai National Institute of Technology (NIT) Surat",
                "Sardar Vallabhbhai National Institute of Technology (NIT) Surat",
            ),
            # --- FABRICATED: this CV contains no doctorate at all ---------
            "phd_institution": _wrap(
                "Indian Institute of Technology Bombay",
                "Ph.D. in Computer Science, Indian Institute of Technology Bombay, 2019",
            ),
            "phd_year": _wrap(2019, "Ph.D. awarded 2019"),
            "phd_status": _wrap("awarded", "Ph.D. in Computer Science, IIT Bombay"),
            # --- FABRICATED: plausible but absent -------------------------
            "research_area": _wrap(
                "federated learning and privacy-preserving machine learning",
                "Research Interests: federated learning",
            ),
            "orcid": _wrap("0000-0002-1825-0097", "ORCID: 0000-0002-1825-0097"),
            "masters_degree": _wrap(
                "Master of Technology, Computer Science and Engineering",
                "Master of Technology, Computer Science and Engineering",
            ),
            "masters_year": _wrap(2025, "Aug 2023 - July 2025"),
            "phd_start_year": _wrap(None, ""),
            "scholar_url": _wrap(None, ""),
            "highest_degree": _wrap("masters", "Master of Technology"),
        },
        "publications": [
            {
                # Entirely invented paper, with a real-sounding venue.
                "title": "Federated Learning for Privacy-Preserving Seismic Prediction",
                "authors": ["Amar Deep", "R. Krishnan"],
                "year": 2024,
                "venue": "IEEE Transactions on Neural Networks",
                "doi": None,
                "status": "published",
                "evidence": "Federated Learning for Privacy-Preserving Seismic "
                            "Prediction, IEEE TNN, 2024. [Published]",
            }
        ],
    }


def test_fabricated_scalars_are_rejected():
    result = validate.validate_record(build_fabricated_record(), SOURCE_CV)
    by_field = {r["field"]: r for r in result["scalar_detail"]}

    for invented in ("phd_institution", "research_area", "orcid"):
        assert by_field[invented]["status"] == "unverified", (
            f"{invented} was fabricated but not rejected"
        )
        # The value must be stripped, not merely annotated.
        assert result["profile"][invented]["value"] is None
        assert result["profile"][invented]["unverified"] is True
        assert result["profile"][invented]["rejected_value"] is not None


def test_fabricated_phd_year_is_rejected():
    result = validate.validate_record(build_fabricated_record(), SOURCE_CV)
    by_field = {r["field"]: r for r in result["scalar_detail"]}
    assert by_field["phd_year"]["status"] == "unverified"
    assert result["profile"]["phd_year"]["value"] is None


def test_truthful_fields_survive():
    """A validator that rejects everything is useless - check it does not."""
    result = validate.validate_record(build_fabricated_record(), SOURCE_CV)
    by_field = {r["field"]: r for r in result["scalar_detail"]}
    for honest in ("full_name", "email", "masters_institution",
                   "masters_degree", "masters_year"):
        assert by_field[honest]["status"] in ("verified", "partial"), (
            f"{honest} is genuinely in the CV but was rejected"
        )
        assert result["profile"][honest]["value"] is not None


def test_fabricated_publication_is_flagged():
    result = validate.validate_record(build_fabricated_record(), SOURCE_CV)
    pub = result["publication_detail"][0]
    assert pub["verdict"] == "unverified"
    assert pub["title_in_source"] is False
    assert any("title" in issue for issue in pub["issues"])


def test_status_guard_downgrades_unmarked_entry():
    """The guard required after Stage 3's measured failure."""
    record = {
        "candidate_id": "X",
        "profile": {},
        "publications": [{
            "title": "Master of Technology",      # real text, so it is found
            "authors": [],
            "year": 2025,
            "venue": "Somewhere",
            "doi": None,
            "status": "published",                # claimed...
            "evidence": "Master of Technology, Computer Science and Engineering",
        }],                                        # ...but no marker present
    }
    result = validate.validate_record(record, SOURCE_CV)
    pub = result["publication_detail"][0]
    assert pub["status_downgraded"] is not None
    assert pub["status_downgraded"]["from"] == "published"
    assert pub["status_final"] == "unknown"
    assert result["publications"][0]["status"] == "unknown"


def test_status_guard_keeps_a_marked_entry():
    """A genuine marker must survive, or the guard is just deleting data."""
    record = {
        "candidate_id": "X",
        "profile": {},
        "publications": [{
            "title": "Master of Technology",
            "authors": [],
            "year": 2025,
            "venue": "Somewhere",
            "doi": None,
            "status": "published",
            "evidence": "Master of Technology, Computer Science [Published]",
        }],
    }
    result = validate.validate_record(record, SOURCE_CV)
    pub = result["publication_detail"][0]
    assert pub["status_downgraded"] is None
    assert pub["status_final"] == "published"


def test_negative_claim_verified_by_absence():
    """'no doctorate' is verified by the CV's silence, not by a quotation."""
    record = {
        "candidate_id": "X",
        "profile": {"phd_status": _wrap("none", "")},
        "publications": [],
    }
    result = validate.validate_record(record, SOURCE_CV)
    by_field = {r["field"]: r for r in result["scalar_detail"]}
    assert by_field["phd_status"]["status"] == "verified"
    assert by_field["phd_status"]["verified_by_absence"] is True


def test_negative_claim_rejected_when_contradicted():
    """Claiming 'none' while the CV shows a doctorate must NOT pass."""
    cv_with_phd = SOURCE_CV + "\nPh.D. in Computer Science, IIT Delhi, 2018\n"
    record = {
        "candidate_id": "X",
        "profile": {"phd_status": _wrap("none", "")},
        "publications": [],
    }
    result = validate.validate_record(record, cv_with_phd)
    by_field = {r["field"]: r for r in result["scalar_detail"]}
    assert by_field["phd_status"]["status"] == "unverified"


# ---------------------------------------------------------------------------
# Doctorate cross-check - the highest-stakes guard in the pipeline
# ---------------------------------------------------------------------------

CV_PHD_IN_PROGRESS = """ARPIT SINGH YADAV
Ph.D. Research Scholar, Department of Electronics and Communication

Academic Qualifications
2020   -  Ph.D.    Indian Institute of Information Technology   7.5 CGPA
2017 - 2019  M.Tech.  Madan Mohan Malaviya University  8.91 CGPA

Research and Professional Experience
Ph.D. Research Scholar                                 Nov 2020 - Present
"""

CV_PHD_AWARDED = """Shiva Dixit
Assistant Professor, Department of Data Science

Education
Ph.D. in Physics                              Aug 2015 - May 2021
Thesis Title: Dynamics of Nonlinear Systems
M. Sc. (Physics)                              2012 - 2014
"""


def test_cross_check_withholds_unsupported_awarded_claim():
    """An open-ended PhD row must not become 'awarded'."""
    record = {
        "candidate_id": "Candidate_02",
        "profile": {
            "phd_status": _wrap("awarded", "2020  -  Ph.D.  Indian Institute"),
            "phd_start_year": _wrap(2020, "2020  -  Ph.D.  Indian Institute"),
            "phd_year": _wrap(2020, "2020  -  Ph.D.  Indian Institute"),
        },
        "publications": [],
    }
    result = validate.validate_record(record, CV_PHD_IN_PROGRESS)
    by_field = {r["field"]: r for r in result["scalar_detail"]}

    assert by_field["phd_status"]["status"] == "unverified"
    assert by_field["phd_status"]["cross_check_failed"] is True
    assert by_field["phd_year"]["status"] == "unverified"
    assert result["validation_summary"]["doctorate_cross_check_failed"] is True
    # The value must be withheld, not silently kept.
    assert result["profile"]["phd_status"]["value"] is None


def test_cross_check_leaves_a_genuine_doctorate_alone():
    """The guard must not damage a real award, or it is just vandalism."""
    record = {
        "candidate_id": "Candidate_04",
        "profile": {
            "phd_status": _wrap("awarded", "Ph.D. in Physics   Aug 2015 - May 2021"),
            "phd_start_year": _wrap(2015, "Ph.D. in Physics   Aug 2015 - May 2021"),
            "phd_year": _wrap(2021, "Ph.D. in Physics   Aug 2015 - May 2021"),
        },
        "publications": [],
    }
    result = validate.validate_record(record, CV_PHD_AWARDED)
    by_field = {r["field"]: r for r in result["scalar_detail"]}

    assert by_field["phd_status"]["status"] == "verified"
    assert result["validation_summary"]["doctorate_cross_check_failed"] is False
    assert result["profile"]["phd_status"]["value"] == "awarded"
    assert result["profile"]["phd_year"]["value"] == 2021


def test_cross_check_catches_implausibly_short_doctorate():
    """Award year within two years of the start is almost always a misread."""
    record = {
        "candidate_id": "X",
        "profile": {
            "phd_status": _wrap("awarded", "Ph.D. in Physics   Aug 2015 - May 2021"),
            "phd_start_year": _wrap(2020, "Ph.D. in Physics   Aug 2015 - May 2021"),
            "phd_year": _wrap(2021, "Ph.D. in Physics   Aug 2015 - May 2021"),
        },
        "publications": [],
    }
    # No "in progress" wording in this CV, so the year gap is the only signal.
    result = validate.validate_record(record, CV_PHD_AWARDED)
    by_field = {r["field"]: r for r in result["scalar_detail"]}
    assert by_field["phd_status"]["status"] == "unverified"
    assert "implausibly close" in by_field["phd_status"]["detail"]


def test_cross_check_never_invents_an_award():
    """The guard may only withhold a claim, never upgrade one."""
    record = {
        "candidate_id": "X",
        "profile": {"phd_status": _wrap("pursuing", "Ph.D. Research Scholar")},
        "publications": [],
    }
    result = validate.validate_record(record, CV_PHD_IN_PROGRESS)
    by_field = {r["field"]: r for r in result["scalar_detail"]}
    assert by_field["phd_status"]["value"] == "pursuing"
    assert result["validation_summary"]["doctorate_cross_check_failed"] is False


def test_completion_year_taken_from_end_of_range():
    """'Aug 2023 - July 2025' is a 2025 completion, not a 2023 one."""
    record = {
        "candidate_id": "Candidate_01",
        "profile": {"masters_year": _wrap(2023, "Aug 2023 - July 2025")},
        "publications": [],
    }
    cv = "Master of Technology, Computer Science\nAug 2023 - July 2025\n"
    result = validate.validate_record(record, cv)
    by_field = {r["field"]: r for r in result["scalar_detail"]}

    assert by_field["masters_year"]["value"] == 2025
    assert by_field["masters_year"]["corrected_from"] == 2023
    assert result["profile"]["masters_year"]["value"] == 2025
    assert result["profile"]["masters_year"]["corrected_from"] == 2023


def test_correct_completion_year_is_left_alone():
    record = {
        "candidate_id": "X",
        "profile": {"masters_year": _wrap(2014, "M. Sc. (Physics)  2012 - 2014")},
        "publications": [],
    }
    cv = "M. Sc. (Physics)  2012 - 2014\n"
    result = validate.validate_record(record, cv)
    by_field = {r["field"]: r for r in result["scalar_detail"]}
    assert by_field["masters_year"]["value"] == 2014
    assert "corrected_from" not in by_field["masters_year"]


def test_open_ended_row_is_not_corrected():
    """
    "2020 - Ph.D." has one year and no end. The completion-year rule must
    not invent one; that case belongs to the doctorate cross-check, which
    WITHHOLDS rather than corrects.
    """
    record = {
        "candidate_id": "Candidate_02",
        "profile": {"phd_year": _wrap(2020, "2020  -  Ph.D.  Indian Institute")},
        "publications": [],
    }
    result = validate.validate_record(record, CV_PHD_IN_PROGRESS)
    by_field = {r["field"]: r for r in result["scalar_detail"]}
    assert "corrected_from" not in by_field["phd_year"]


# ---------------------------------------------------------------------------
# Candidate_06 regressions: classification and the authorship contradiction
# ---------------------------------------------------------------------------

def test_non_academic_employer_is_classified_as_industry():
    """schema.py defines industry as "any non-academic employer"."""
    positions, notes = validate.review_position_categories([
        {"title": "Tech Mentor Intern", "organisation": "Kalvium",
         "category": "other"},
    ])
    assert positions[0]["category"] == "industry"
    assert positions[0]["category_proposed_by_model"] == "other"
    assert notes and "other -> industry" in notes[0]


def test_academic_employer_keeps_teaching_classification():
    positions, _ = validate.review_position_categories([
        {"title": "Assistant Professor",
         "organisation": "Lovely Professional University",
         "category": "teaching"},
    ])
    assert positions[0]["category"] == "teaching"
    assert "category_proposed_by_model" not in positions[0]


def test_role_without_an_employer_is_flagged_not_guessed():
    positions, notes = validate.review_position_categories([
        {"title": "Consultant", "organisation": None, "category": "other"},
    ])
    assert positions[0]["category"] == "other"      # unchanged
    assert any("no employer named" in n for n in notes)


def test_cv_listing_publications_without_authors_still_validates():
    """
    Candidate_06's CV lists papers by title only. Her name appears in the
    header and nowhere near the titles, which previously marked every
    publication 'unverified' and excluded it from credit - while OpenAlex
    returned an exact match naming her as an author.
    """
    # Padded so the header name sits far from the title, as in the real
    # 3,181-character CV. Without padding the proximity window trivially
    # succeeds and the test proves nothing.
    cv = ("Deepali Kumari\n\nEducation\n"
          + ("SRM University, Andhra Pradesh. M.Tech in AI and ML. " * 20)
          + "\n\nResearch Publications\n\n"
          "ViTDehazer: A Vision Transformer-Based Approach for Effective "
          "Image Dehazing   [29 April 2025]\n")
    record = {
        "candidate_id": "Candidate_06",
        "profile": {},
        "publications": [{
            "title": "ViTDehazer: A Vision Transformer-Based Approach for "
                     "Effective Image Dehazing",
            "authors": ["Deepali Kumari"],
            "year": 2025, "venue": "OCIT", "status": "published",
            "evidence": "ViTDehazer: A Vision Transformer-Based Approach for "
                        "Effective Image Dehazing   [29 April 2025]",
        }],
    }
    result = validate.validate_record(record, cv)
    detail = result["publication_detail"][0]

    assert detail["verdict"] == "verified", detail["issues"]
    assert not any("author" in issue for issue in detail["issues"])
    # Still recorded for the reviewer, just not treated as a failure.
    assert any("co-author" in flag for flag in detail["flags"])


def test_fabricated_publication_is_still_caught():
    """Relaxing the author rule must not weaken the title check."""
    cv = "Deepali Kumari\n\nResearch Publications\n\nViTDehazer: Image Dehazing\n"
    record = {
        "candidate_id": "X", "profile": {},
        "publications": [{
            "title": "Attention Is All You Need", "authors": ["Deepali Kumari"],
            "year": 2017, "status": "published", "evidence": "invented line",
        }],
    }
    result = validate.validate_record(record, cv)
    detail = result["publication_detail"][0]
    assert detail["verdict"] == "unverified"
    assert any("title not found" in issue for issue in detail["issues"])
