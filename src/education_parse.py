"""
Rule-based reading of a CV's education entries.

WHY THIS EXISTS
  The language model reads degrees well from prose and badly from tables.
  Application-form CVs print qualifications as a grid ("Sl.No. Degree ...
  Year of Passing") whose cells wrap over several lines, and the model
  dropped whole rows, read a passing year as a start year, and called a
  doctorate finished in a past year "pursuing". Nothing deterministic stood
  behind it, so each of those misreadings became a zero.

  This module reads the degree lines themselves - the qualification name,
  the dates printed on the same line, and the few lines that belong to the
  same entry - and reports what the CV states. Stage 4 uses it to fill and
  correct the model's fields, never to invent one: every value it returns
  is quoted from a line of the CV, which is kept as the evidence.

WHAT COUNTS AS COMPLETED
  A qualification is complete when its own entry shows a finished period:
  a closed date range, or a single passing year, ending on or before the
  as-of date, with no in-progress wording ("pursuing", "expected",
  "present", "thesis submitted") inside the entry. No keyword such as
  "awarded" is required - a CV that prints "Ph.D. ... 2020 - 2024" is
  telling us the degree took place and ended.

  For a doctorate, the supporting context the CV gives (a thesis title, a
  thesis award, a postdoctoral post, "Post Ph.D. experience", the "Dr."
  title) is recorded beside the date as corroboration, so a reviewer can
  see what the inference rests on. A completion resting on dates alone is
  still credited, and flagged for confirmation.

NO RULE HERE NAMES A CANDIDATE, A COLLEGE OR A CV.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import degrees
import schema

LEVELS = ("doctorate", "masters", "bachelors")

# ---------------------------------------------------------------------------
# Qualification names, as printed
# ---------------------------------------------------------------------------
# Longest forms first within each level, so "Master of Computer
# Applications" is not read as "Master of Computer".
#
# Short forms that are also English words or initials ("BE", "MS", "BA",
# "MA", "ME", "BS") are matched CASE-SENSITIVELY and only in capitals or
# with dots, so "Be" at the start of a sentence is not a degree.

_CI = re.IGNORECASE

_DOCTORATE_FORMS = [
    re.compile(r"doctor\s+of\s+philosophy", _CI),
    re.compile(r"ph\s*\.\s*d\.?|phd\.?|ph\s+d", _CI),
    re.compile(r"d\s*\.\s*phil\.?|dphil", _CI),
    re.compile(r"doctorate", _CI),
]

_MASTERS_FORMS = [
    re.compile(r"integrated\s+m\s*\.?\s*(?:tech|sc|s)\.?", _CI),
    re.compile(r"master(?:'?s)?\s+of\s+(?:computer\s+applications|business"
               r"\s+administration|science\s+by\s+research|[a-z]+)", _CI),
    # "Masters (M. Tech.)" - the bracket names the actual degree.
    re.compile(r"master(?:'?s)?\s*\(\s*m\s*\.?[^)]{1,12}\)", _CI),
    re.compile(r"master(?:'?s)?\s+(?:degree\s+)?in(?=\s)", _CI),
    re.compile(r"m\s*\.\s*tech\.?|mtech\.?", _CI),
    re.compile(r"m\s*\.\s*sc\.?|msc\.?", _CI),
    re.compile(r"m\s*\.\s*phil\.?|mphil\.?", _CI),
    re.compile(r"m\s*\.\s*c\s*\.\s*a\.?|mca\.?", _CI),
    re.compile(r"m\s*\.\s*b\s*\.\s*a\.?|mba\.?", _CI),
    re.compile(r"m\s*\.\s*com\.?", _CI),
    re.compile(r"M\s*\.\s*E\.?|ME(?=\s)"),
    re.compile(r"M\s*\.\s*S\.?(?!\s*c)|MS(?=[\s,(])"),
    re.compile(r"M\s*\.\s*A\.?|MA(?=\s)"),
]

_BACHELORS_FORMS = [
    re.compile(r"bachelor(?:'?s)?\s+of\s+(?:computer\s+applications|[a-z]+)",
               _CI),
    re.compile(r"bachelor(?:'?s)?\s*\(\s*b\s*\.?[^)]{1,12}\)", _CI),
    re.compile(r"bachelor(?:'?s)?\s+(?:degree\s+)?in(?=\s)", _CI),
    re.compile(r"b\s*\.\s*tech\.?|btech\.?", _CI),
    re.compile(r"b\s*\.\s*sc\.?|bsc\.?", _CI),
    re.compile(r"b\s*\.\s*c\s*\.\s*a\.?|bca\.?", _CI),
    re.compile(r"b\s*\.\s*com\.?", _CI),
    re.compile(r"B\s*\.\s*E\.?|BE(?=\s)"),
    re.compile(r"B\s*\.\s*S\.?(?!\s*c)|BS(?=[\s,(])"),
    re.compile(r"B\s*\.\s*A\.?|BA(?=\s)"),
]

FORMS = {
    "doctorate": _DOCTORATE_FORMS,
    "masters": _MASTERS_FORMS,
    "bachelors": _BACHELORS_FORMS,
}

# A degree name must END at a word boundary, so "BSc" does not match the
# start of "BScience" nor "B.E" the start of "B.Ed".
_TOKEN_END = re.compile(r"(?=$|[\s,;:()\[\]/|\-–—])")


def match_degree_at(text: str, pos: int = 0) -> tuple[str, str] | None:
    """(level, verbatim name) for a qualification starting at `pos`."""
    for level in LEVELS:
        for pattern in FORMS[level]:
            m = pattern.match(text, pos)
            if not m:
                continue
            if not m.group(0).endswith(".") and not _TOKEN_END.match(text, m.end()):
                continue
            return level, m.group(0).strip()
    return None


def find_degree_token(text: str | None, level: str) -> str | None:
    """The first qualification name of `level` anywhere in `text`, verbatim."""
    if not text:
        return None
    for start in range(len(text)):
        if start and text[start - 1].isalnum():
            continue
        hit = match_degree_at(text, start)
        if hit and hit[0] == level:
            return hit[1]
    return None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?"
          r"|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?"
          r"|nov(?:ember)?|dec(?:ember)?)")
_MONTH_NUM = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

_POINT = (rf"(?:{_MONTH}\.?,?\s*(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s*)?"
          rf"|\d{{1,2}}[/.-]\d{{1,2}}[/.-]|\d{{1,2}}[/.-])?(?:19|20)\d{{2}}")
_OPEN_END = (r"present|current(?:ly)?|now|ongoing|continuing|till\s+date"
             r"|to\s+date|date|onwards|pursuing|expected")
POINT = re.compile(_POINT, re.IGNORECASE)
DATE_RANGE = re.compile(
    rf"(?P<start>{_POINT})\s*(?:-+|–|—|to|till|until)\s*"
    rf"(?P<end>{_POINT}|{_OPEN_END})", re.IGNORECASE)
YEAR = re.compile(r"(?<![\d.])((?:19|20)\d{2})(?![\d.])")


def _point(text: str) -> tuple[int, int] | None:
    """(year, month) for one end of a range; month 0 when unstated."""
    year = re.search(r"(19|20)\d{2}", text)
    if not year:
        return None
    month_name = re.search(_MONTH, text, re.IGNORECASE)
    if month_name:
        month = _MONTH_NUM[month_name.group(0)[:3].lower()]
    else:
        numeric = re.match(r"\s*(?:\d{1,2}[/.-])?(\d{1,2})[/.-]", text)
        month = int(numeric.group(1)) if numeric else 0
        if not 0 <= month <= 12:
            month = 0
    return int(year.group(0)), month


# ---------------------------------------------------------------------------
# Entry boundaries and wording
# ---------------------------------------------------------------------------

EDUCATION_HEADING = re.compile(
    r"\b(education|educational|qualifications?|academic\s+(?:background"
    r"|record|details|credentials))\b", _CI)

OTHER_HEADING = re.compile(
    r"^\s*(?:details\s+of\s+)?(experience|employment|work|professional"
    r"|positions?|appointments?|publications?|research|projects?|teaching"
    r"|awards?|honou?rs|achievements?|skills?|patents?|interests?"
    r"|profile|summary|objective|certifications?|references?|referees?"
    r"|declaration|titles?|memberships?|personal|languages?|courses?"
    r"|workshops?|conferences?|journals?|books?|funding|grants?"
    r"|fellowships?|scholarships?|supervision|students?|thesis\s+guided"
    r"|total\s+research|other\s+information|hobbies)\b", _CI)

# Wording that, inside a degree's OWN entry, means it is not finished.
# Word-bounded: "presented" and "representative" are not "present".
IN_PROGRESS = re.compile(
    r"\b(pursuing|perusing|ongoing|on-going|in\s+progress|continuing|present"
    r"|currently|till\s+date|to\s+date|expected|anticipated|awaiting"
    r"|not\s+yet|enrolled|registered|research\s+scholar|ph\.?\s?d\.?\s+scholar"
    r"|doctoral\s+scholar|under\s+evaluation|under\s+examination)\b", _CI)

# A degree name followed by one of these is a heading or a statement about
# other people, not a qualification the candidate holds.
NOT_AN_ENTRY_AFTER = re.compile(
    r"^\s*[:\-–]?\s*(thesis|dissertation|students?|scholars?|guided"
    r"|supervis\w*|mentor\w*|experience|positions?|fellowship|admission"
    r"|courses?|level|projects?)\b", _CI)

# Sections about OTHER people: degrees named there are theirs.
OTHER_PEOPLE_HEADING = re.compile(
    r"\b(supervis\w*|students?|mentor\w*|referees?|references?|guided)\b", _CI)

INSTITUTION_WORDS = re.compile(
    r"(universit|institut|college|\biit\b|\bnit\b|\biiit\b|\biisc\b"
    r"|\biiser\b|school|academy|polytechnic|vidyalaya|vishwavidyalaya"
    r"|mahavidyalaya|\bcampus\b)", _CI)

_NUMERIC_CELL = re.compile(r"^[\d.,%/\s:]+$|^(cgpa|gpa|percentage|marks)\b",
                           _CI)
# "1", "1.", "(a)", "iv)" or a bullet - never a word, so "MSc." is not a label.
_LEADING_LABEL = re.compile(
    r"^\s*(?:\(?(?:\d{1,2}|[a-z]|[ivx]{1,4})[.)]|\d{1,2}|[•●▪*\-–])\s+", _CI)
_AFTER_LEADING_DATE = re.compile(r"\s*(?:[-–—:|,]\s*)?")
CELL_SPLIT = re.compile(r"\s{2,}|\t|\s\|\s")
BLOCK_MAX_LINES = 10


@dataclass
class EducationEntry:
    """One qualification as the CV prints it."""
    level: str
    name: str
    line: str                       # the verbatim degree line - the evidence
    line_index: int
    block: str = ""                 # the entry's own lines, joined
    in_education_section: bool = False
    subject: str | None = None
    institution: str | None = None
    start: tuple[int, int] | None = None
    end: tuple[int, int] | None = None
    open_ended: bool = False
    single_year: bool = False
    status: str | None = None       # completed | pursuing | thesis_submitted | None
    year: int | None = None         # completion year when completed
    basis: str = ""                 # why the status was concluded
    markers: list = field(default_factory=list)
    needs_confirmation: bool = False

    @property
    def has_dates(self) -> bool:
        return self.end is not None or self.start is not None


def _clean(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _is_heading(line: str) -> bool:
    words = line.split()
    if not words or len(words) > 7 or YEAR.search(line):
        return False
    return bool(OTHER_HEADING.match(line) or EDUCATION_HEADING.search(line))


# A side heading: "Education      • Indian Institute of ..." - the heading
# in a left margin column with the section's first line beside it.
SIDE_EDUCATION_HEADING = re.compile(
    r"^\s*(education|educational\s+qualifications?|academic\s+"
    r"(?:background|qualifications?|details))\s{2,}\S", _CI)

_STRONG_INSTITUTION = re.compile(
    r"(universit|institute\s+of|\biit\b|\bnit\b|\biiit\b|\biisc\b"
    r"|\biiser\b|\bbits\b|\bmnit\b)", _CI)
_FRAGMENT = re.compile(r"(\bof|\band|&)\s*$|^(college|school|institute"
                       r"|university|academy)$", _CI)


def _pick_institution(cells: list[str]) -> str | None:
    """The institution cell, preferring the awarding body to a fragment."""
    named = [c for c in cells if INSTITUTION_WORDS.search(c)
             and not _FRAGMENT.search(c)]
    strong = [c for c in named if _STRONG_INSTITUTION.search(c)]
    return (strong or named or [None])[0]


def _institution_line(text: str) -> str | None:
    """A whole line naming an institution, as in 'Degree / Institution'."""
    text = re.sub(r"^\s*[•●▪*\-–]\s*", "", text).strip()
    if not text or not INSTITUTION_WORDS.search(text):
        return None
    head = CELL_SPLIT.split(text)
    # A table row has several cells; a line under a degree has one, or one
    # plus a date.
    if len(head) > 2 or len(head[0].split()) > 14:
        return None
    if len(head) == 2 and not YEAR.search(head[1]):
        return None
    return head[0].strip(" ,")


def _degree_at_line_head(line: str) -> tuple[str, str, int] | None:
    """
    (level, name, end) when the line OPENS with a qualification, after an
    optional serial number, bullet, or leading date range.
    """
    pos = 0
    label = _LEADING_LABEL.match(line)
    if label:
        pos = label.end()
    leading = DATE_RANGE.match(line, pos) or POINT.match(line, pos)
    if leading:
        pos = _AFTER_LEADING_DATE.match(line, leading.end()).end()
    while pos < len(line) and line[pos] == " ":
        pos += 1
    hit = match_degree_at(line, pos)
    if not hit:
        return None
    return hit[0], hit[1], pos + len(hit[1])


def _degree_at_cell_start(line: str) -> tuple[str, str, int] | None:
    """A qualification opening any table cell of the line."""
    for m in re.finditer(r"(?:^\s*|\s{2,}|\t|\s\|\s)(?=\S)", line):
        hit = match_degree_at(line, m.end())
        if hit:
            return hit[0], hit[1], m.end() + len(hit[1])
    return None


def _subject_and_institution(rest: str) -> tuple[str | None, str | None]:
    """Subject and institution from the text after the degree name."""
    rest = DATE_RANGE.sub("  ", rest)
    in_form = re.match(r"^\s*[,.]?\s*(?:in\s+|\()\s*([^()]+?)(?:\)|\s{2,}|$)",
                       rest, _CI)
    cells = [c.strip(" ,.;:-–—()") for c in CELL_SPLIT.split(rest)]
    cells = [c for c in cells if len(c) > 1 and not _NUMERIC_CELL.match(c)
             and not YEAR.fullmatch(c)]

    institution = _pick_institution(cells)
    if in_form and not INSTITUTION_WORDS.search(in_form.group(1)):
        subject = in_form.group(1).strip(" ,.;:-")
    else:
        # Prefer a cell whose subject this project can recognise: a table
        # row often carries a wrapped fragment ("Artificial") before the
        # readable one ("Mathematics").
        before = []
        for c in cells:
            if c == institution:
                break
            before.append(c)
        subject = next((c for c in before if degrees.subject_family(c)),
                       before[0] if before else None)
    return (subject if subject and len(subject) > 1 else None), institution


# "2020 - Ph.D. ..." or "2020 -" at the end of a line: a range with nothing
# after the dash. It is the most dangerous shape on an education line - a
# start year that looks like a completion year - so it is recognised as
# OPEN before any lone year is read as a year of passing.
OPEN_RANGE = re.compile(
    rf"(?P<start>{_POINT})\s*(?:-+|–|—|to|onwards|till)\s*(?!\s*{_POINT})",
    re.IGNORECASE)


def _dates_from(text: str) -> dict:
    """Range or years found in `text`."""
    rng = DATE_RANGE.search(text)
    if rng:
        end = _point(rng.group("end"))
        return {"start": _point(rng.group("start")), "end": end,
                "open": end is None, "single": False}
    dangling = OPEN_RANGE.search(text)
    if dangling:
        return {"start": _point(dangling.group("start")), "end": None,
                "open": True, "single": False}
    years = sorted({int(y) for y in YEAR.findall(text)})
    if not years:
        return {}
    if len(years) == 1:
        return {"start": None, "end": (years[0], 0), "open": False,
                "single": True}
    return {"start": (years[0], 0), "end": (years[-1], 0), "open": False,
            "single": False}


def _conclude(entry: EducationEntry, as_of: date) -> None:
    """Set status / year / basis from the entry's own lines."""
    block = re.sub(r"\s+", " ", entry.block).lower()
    submitted = next((m for m in schema.THESIS_SUBMITTED_MARKERS
                      if m in block), None)
    progress = IN_PROGRESS.search(entry.block)

    if submitted:
        entry.status = "thesis_submitted"
        entry.basis = f"the entry reads {submitted!r}"
        entry.markers.append(submitted)
        return
    if progress or entry.open_ended:
        word = progress.group(0) if progress else "an open-ended date range"
        entry.status = "pursuing"
        entry.basis = f"the entry reads {word!r}"
        entry.markers.append(word)
        return
    if entry.end is None:
        entry.basis = "the entry prints no date"
        return
    end_year, end_month = entry.end
    if end_year > as_of.year or (end_year == as_of.year and end_month
                                 and end_month > as_of.month):
        entry.status = "pursuing"
        entry.basis = (f"the entry ends in {end_year}, after the as-of "
                       f"date {as_of.isoformat()}")
        return
    entry.status, entry.year = "completed", end_year
    entry.basis = (
        f"the entry gives {end_year} as its year with no in-progress "
        f"wording, read as the year of passing"
        if entry.single_year else
        f"the entry's period closes in {end_year}, on or before the as-of "
        f"date {as_of.isoformat()}, with no in-progress wording")
    # The current year with no month printed: probably passed, but the
    # CV does not say whether before or after today.
    if end_year == as_of.year and not end_month:
        entry.needs_confirmation = True
        entry.basis += ("; the year is the current one and no month is "
                        "printed, so please confirm the degree is conferred")


def parse_education(source_text: str, as_of: date | None = None) -> list[EducationEntry]:
    """Every qualification entry the CV prints, in document order."""
    as_of = as_of or date.today()
    lines = [_clean(line).rstrip() for line in (source_text or "").splitlines()]

    in_education = in_other_people = False
    found: dict[int, tuple] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if _is_heading(stripped):
            in_education = bool(EDUCATION_HEADING.search(stripped))
            in_other_people = bool(OTHER_PEOPLE_HEADING.search(stripped))
            continue
        if in_other_people:
            continue
        if SIDE_EDUCATION_HEADING.match(line):
            in_education = True
        hit = _degree_at_line_head(line)
        if not hit and in_education:
            hit = _degree_at_cell_start(line)
        if not hit:
            continue
        level, name, end = hit
        if NOT_AN_ENTRY_AFTER.match(line[end:]):
            continue
        # Outside an education section a bare mention ("PhD - Computer
        # Science" under the name) is not an entry unless it carries a date.
        if not in_education and not YEAR.search(line):
            continue
        found[i] = (level, name, end, in_education)

    entries: list[EducationEntry] = []
    for i, (level, name, end, in_edu) in found.items():
        line = lines[i]
        block = [line]
        for j in range(i + 1, min(len(lines), i + 1 + BLOCK_MAX_LINES)):
            nxt = lines[j]
            if not nxt.strip() or j in found or _is_heading(nxt.strip()):
                break
            block.append(nxt)

        entry = EducationEntry(level=level, name=name, line=line.strip(),
                               line_index=i, block="\n".join(block),
                               in_education_section=in_edu)
        entry.subject, entry.institution = _subject_and_institution(line[end:])

        # "Institution     dates" on the line ABOVE the degree is a common
        # layout. Read it only when that line names an institution, is not
        # itself a degree line, and is not a heading - so the previous
        # entry's text cannot leak in.
        above = lines[i - 1] if i and (i - 1) not in found else ""
        above_institution = (_institution_line(above)
                             if above.strip() and not _is_heading(above.strip())
                             else None)

        # A wrapped table row ("G. B. Pant / University of / Agriculture
        # and ...") is NOT reassembled: its cells are centred, so a
        # fragment cannot be assigned to its column reliably, and a guessed
        # institution would decide a tier. It stays unidentified, which
        # scores at the base rate and is flagged for the reviewer.
        if not entry.institution:
            for follow in block[1:3]:
                entry.institution = _institution_line(follow)
                if entry.institution:
                    break
        if not entry.institution:
            entry.institution = above_institution

        dates = _dates_from(line[end:]) or _dates_from(line[:end])
        if not dates:
            for follow in block[1:4]:
                dates = _dates_from(follow)
                if dates:
                    break
        if not dates and above_institution:
            dates = _dates_from(above)
        if dates:
            entry.start, entry.end = dates["start"], dates["end"]
            entry.open_ended, entry.single_year = dates["open"], dates["single"]
        _conclude(entry, as_of)
        entries.append(entry)
    return entries


def best_entry(entries: list[EducationEntry], level: str) -> EducationEntry | None:
    """
    The entry that best represents the qualification at `level`: inside
    the education section, dated, completed and latest, in that order.
    """
    pool = [e for e in entries if e.level == level]
    if not pool:
        return None
    return max(pool, key=lambda e: (
        e.in_education_section, e.has_dates, e.status == "completed",
        e.year or (e.end or (0, 0))[0], -e.line_index))


# ---------------------------------------------------------------------------
# Corroboration for a completed doctorate
# ---------------------------------------------------------------------------

_CORROBORATION = (
    (re.compile(r"(\bthesis\s*(?:title)?\s*:|title\s+of\s+(?:the\s+)?"
                r"(?:ph\.?\s?d\.?\s+)?thesis|titles?\s+pg\s*/\s*ph\.?\s?d"
                r"|ph\.?\s?d\.?\s+thesis\b(?!\s+guided))", _CI),
     "the CV gives the doctoral thesis"),
    (re.compile(r"\b(best\s+(?:ph\.?\s?d\.?\s+)?thesis|thesis\s+award)", _CI),
     "the CV records an award for the doctoral thesis"),
    (re.compile(r"\bpost[\s-]?doc", _CI),
     "the CV lists a postdoctoral appointment"),
    (re.compile(r"\bpost[\s-]?ph\.?\s?d\.?\s+(?:exp\.?|experience)\s*:?\s*[1-9]",
                _CI),
     "the CV states post-PhD experience"),
)
_DR_TITLE = re.compile(r"^\s*[Dd][Rr]\.?\s+[A-Z]", re.M)


def doctorate_corroboration(source_text: str) -> list[str]:
    """Context the CV gives, beyond the dates, that a doctorate was completed."""
    text = _clean(source_text)
    found = [label for pattern, label in _CORROBORATION if pattern.search(text)]
    if _DR_TITLE.search("\n".join(text.splitlines()[:5])):
        found.append("the candidate uses the title 'Dr.'")
    return found
