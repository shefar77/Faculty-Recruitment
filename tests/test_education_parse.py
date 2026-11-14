"""
The rule-based education reader: degree names, completion, and scope.

Synthetic CV fragments only - no candidate, college or demo CV is named.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import education_parse as ep   # noqa: E402
import validate                # noqa: E402

AS_OF = date(2026, 11, 14)


def parse(text: str):
    return ep.parse_education(text, AS_OF)


def only(text: str, level: str):
    entry = ep.best_entry(parse(text), level)
    assert entry is not None, f"no {level} entry in {text!r}"
    return entry


# ---------------------------------------------------------------------------
# Naming variations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,level", [
    ("B.E.", "bachelors"), ("B.Tech.", "bachelors"), ("B. Tech", "bachelors"),
    ("BTech", "bachelors"), ("B.Sc.", "bachelors"), ("BSc.", "bachelors"),
    ("B.S.", "bachelors"), ("BCA", "bachelors"),
    ("Bachelor of Engineering", "bachelors"),
    ("Bachelor of Science", "bachelors"), ("Bachelor (B. Sc.)", "bachelors"),
    ("M.E.", "masters"), ("M.Tech.", "masters"), ("M. Tech.", "masters"),
    ("M.Sc", "masters"), ("MSc.", "masters"), ("M.S.", "masters"),
    ("MCA", "masters"), ("M.Phil", "masters"),
    ("Master of Technology", "masters"), ("Master of Science", "masters"),
    ("Masters (M. Tech.)", "masters"),
    ("Ph.D.", "doctorate"), ("PhD", "doctorate"), ("Ph. D.", "doctorate"),
    ("Doctor of Philosophy", "doctorate"), ("D.Phil", "doctorate"),
])
def test_every_common_spelling_is_recognised(name, level):
    entry = only(f"Education\n{name}  Computer Science   Some University   2015 - 2019\n",
                 level)
    assert entry.status == "completed"
    assert entry.year == 2019


@pytest.mark.parametrize("line", [
    "Be it resolved that the committee meets annually 2019",
    "BEST paper award 2019",
    "B.Ed. Mathematics   2012",
])
def test_words_that_merely_start_like_a_degree_are_not_degrees(line):
    assert not [e for e in parse(f"Education\n{line}\n")
                if e.level == "bachelors"]


# ---------------------------------------------------------------------------
# Completion: what the entry itself says
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row,status,year", [
    ("Ph.D. Physics, Some Institute        Sep 2020 - Sep 2024", "completed", 2024),
    ("Ph.D. Physics, Some Institute        2020 - 2024", "completed", 2024),
    ("Ph.D.   Physics   Some University    2023    8.9", "completed", 2023),
    ("Ph.D. Physics, Some Institute        2021 - Present", "pursuing", None),
    ("Ph.D. Physics, Some Institute        2021 - till date", "pursuing", None),
    ("2020   -  Ph.D.  Some Institute   7.5 CGPA", "pursuing", None),
    ("Ph.D. Physics (expected 2027)", "pursuing", None),
    ("Ph.D. Physics, Some Institute        2022 - 2028", "pursuing", None),
    ("Ph.D. (Thesis Submitted), Some Institute   2021 - 2026", "thesis_submitted", None),
    ("Ph.D. Physics, Some Institute", None, None),
])
def test_completion_is_read_from_the_entry(row, status, year):
    entry = only(f"Education\n{row}\n", "doctorate")
    assert entry.status == status
    assert entry.year == year


def test_no_keyword_is_needed_for_a_finished_period():
    entry = only("Education\nDoctor of Philosophy in Chemistry   July 2015 - June 2020\n",
                 "doctorate")
    assert entry.status == "completed"
    assert "awarded" not in entry.line.lower()


def test_the_current_year_without_a_month_is_completed_but_flagged():
    entry = only("Education\nPh.D.   Physics   Some University   2026\n", "doctorate")
    assert entry.status == "completed"
    assert entry.needs_confirmation is True


def test_wording_in_the_next_entry_does_not_leak_back():
    text = ("Education\nPh.D. Physics, Some Institute   2015 - 2020\n\n"
            "Experience\nResearch Scholar   2021 - Present\n")
    assert only(text, "doctorate").status == "completed"


def test_presented_is_not_present():
    text = ("Education\nPh.D. Physics, Some Institute   2015 - 2020\n"
            "Work presented at a representative workshop\n")
    assert only(text, "doctorate").status == "completed"


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------

def test_institution_and_dates_on_the_line_above():
    text = ("Education\nSome National Institute of Technology    Aug 2021 - July 2023\n"
            "Master of Technology, Computer Science and Engineering   CGPA: 8.1\n")
    entry = only(text, "masters")
    assert entry.year == 2023
    assert entry.institution.startswith("Some National Institute of Technology")
    assert entry.subject == "Computer Science and Engineering"


def test_institution_on_the_line_below():
    text = ("Education\nMaster of Science in Physics        July 2016 - August 2018\n"
            "Some University, Some City\n")
    entry = only(text, "masters")
    assert entry.institution == "Some University, Some City"
    assert entry.subject == "Physics"


def test_table_row_with_a_year_of_passing_column():
    text = ("Particulars of Educational Qualification\n"
            "Sl.No. Degree   Branch      Name of the University   Year of Passing\n"
            "  1   B.Sc.     Mathematics     Some University      2016     72.00\n"
            "  2   M.Sc      Mathematics     Some University      2018      0.00\n")
    entries = parse(text)
    assert [(e.level, e.year) for e in entries] == [("bachelors", 2016),
                                                    ("masters", 2018)]


def test_degrees_in_a_supervision_section_are_not_the_candidates():
    text = ("Education\nPh.D. Physics, Some Institute   2015 - 2020\n\n"
            "Students Supervised\nM.Tech   Mentored: A. Student, 2023 thesis titled X\n")
    assert [e.level for e in parse(text)] == ["doctorate"]


def test_a_bare_mention_outside_education_is_not_an_entry():
    assert parse("Some Name\nPhD - Computer Science and Engineering\n") == []


# ---------------------------------------------------------------------------
# Corroboration
# ---------------------------------------------------------------------------

def test_corroborating_context_is_collected():
    text = ("Dr. Some Name\nEducation\nPh.D. Physics   2015 - 2020\n"
            "Thesis: On Something\nExperience\nPostdoctoral Fellow  2020 - 2022\n")
    found = ep.doctorate_corroboration(text)
    assert any("thesis" in f for f in found)
    assert any("postdoctoral" in f for f in found)
    assert any("Dr." in f for f in found)


# ---------------------------------------------------------------------------
# Degree-name recovery in Stage 4
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stated,printed", [
    ("masters", "Master of Science in Physics"),
    ("btech", "B.E.         Information Technology"),
])
def test_a_normalised_degree_name_is_recovered_from_its_span(stated, printed):
    field = "masters_degree" if "Master" in printed else "bachelors_degree"
    row = validate.check_scalar(field, {"value": stated, "evidence": printed},
                                validate.normalise(printed))
    assert row["status"] == "verified"
    assert row["corrected_from"] == stated
    assert row["value"] in printed


# ---------------------------------------------------------------------------
# Titles split across table columns
# ---------------------------------------------------------------------------

TABLE = validate.normalise("""
 8   Journal    Facial Emotion      Pandey,   Kumar, A.    SN Computer   2022  Non
     International  Intensity: A    Science   SCI   A.  Published   Fusion Way.
 2   Journal   Maximal Density   Ram   Neha   Taiwanese   2020   SCI
     of Sets with    Journal of    Missing    Di!erences and   Various Coloring
""")


@pytest.mark.parametrize("title", [
    "Facial Emotion Intensity: A Fusion Way.",
    "Maximal Density of Sets with Missing Differences and Various Coloring",
])
def test_a_title_interleaved_with_other_columns_is_found(title):
    found, score = validate.appears_in(title, TABLE, allow_scattered=True)
    assert found and score == validate.SCATTERED_MATCH_SCORE


def test_scattered_matching_is_off_unless_asked_for():
    found, _ = validate.appears_in("Facial Emotion Intensity: A Fusion Way.", TABLE)
    assert not found


def test_words_spread_across_a_whole_document_do_not_make_a_title():
    filler = " ".join(["lorem"] * 400)
    text = validate.normalise(f"deep {filler} learning {filler} methods {filler} survey")
    assert not validate.appears_in("Deep Learning Methods Survey", text,
                                   allow_scattered=True)[0]


def test_short_titles_are_not_eligible_for_scattered_matching():
    text = validate.normalise("graph one two three four theory")
    assert not validate.appears_scattered("Graph Theory", text)
