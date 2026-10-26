"""
Section-boundary detection for position extraction.

Pure text handling, no model calls. These guard the boundary rules that
decide which part of a CV the positions prompt ever sees - a mistake here
silently loses whole jobs, which is how Candidate_06's internship
disappeared.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import extract_fields  # noqa: E402


def test_prose_containing_a_heading_word_does_not_end_a_section():
    """
    The bug: "...aligned with outcome-based education practices." matched
    the end-of-section pattern on the word "education", truncating the
    Experience section mid-sentence and discarding the job that followed.
    """
    cv = (
        "Experience\n"
        "Lovely Professional University            Oct 4, 2024 - Present\n"
        "Assistant Professor\n"
        "    - Involved in academic delivery, student mentoring, aligned with\n"
        "    outcome-based education practices.\n"
        "\n"
        "Kalvium                                   July 2024 - Aug. 2024\n"
        "Tech Mentor Intern\n"
        "    - Provided structured academic support to students.\n"
        "Patents\n"
        "8+ Patents Published\n"
    )
    sections = extract_fields.find_position_sections(cv)
    assert len(sections) == 1
    _, body = sections[0]

    assert "Kalvium" in body, "the second job was cut off by a prose line"
    assert "Tech Mentor Intern" in body
    # ...but the genuine next heading still ends it.
    assert "8+ Patents Published" not in body


def test_real_headings_still_end_a_section():
    cv = (
        "Experience\n"
        "Acme Corp                                 2020 - 2022\n"
        "Engineer\n"
        "Education\n"
        "B.Tech, Somewhere University\n"
    )
    _, body = extract_fields.find_position_sections(cv)[0]
    assert "Acme Corp" in body
    assert "B.Tech" not in body


def test_patents_heading_ends_a_section():
    """Added because Candidate_06's CV puts Patents straight after jobs."""
    cv = "Experience\nAcme                2020 - 2022\nEngineer\nPatents\n[12345]\n"
    _, body = extract_fields.find_position_sections(cv)[0]
    assert "Acme" in body
    assert "12345" not in body


def test_multiple_experience_sections_are_all_found():
    cv = (
        "Experience\nUni A   2024 - Present\nAssistant Professor\n"
        "Internship\nCorp B  2023 - 2024\nIntern\n"
        "Education\nB.Tech\n"
    )
    sections = extract_fields.find_position_sections(cv)
    assert [h for h, _ in sections] == ["Experience", "Internship"]
    assert "B.Tech" not in sections[1][1]


def test_no_experience_heading_returns_nothing():
    """The caller falls back to the whole CV rather than inventing a section."""
    assert extract_fields.find_position_sections("Education\nB.Tech\n") == []
