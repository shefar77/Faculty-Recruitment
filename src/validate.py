"""
STAGE 4 - validation / anti-hallucination.

Stage 3's format:"json" guaranteed the model's output PARSES. It guaranteed
nothing about whether any of it is TRUE. This module is the check: for every
extracted string, does it (or a normalised form) actually occur in the CV
text? Anything that does not is marked "unverified" and its value is
REMOVED, not kept with a warning attached. A value nobody can trace to the
source has no business reaching a score.

THREE KINDS OF FIELD, VALIDATED THREE WAYS
  Treating every field identically would be wrong in both directions.

  free_text  (names, institutions, venues, titles)
      Must appear in the CV, near-verbatim. These are the fields a model
      invents, so they get the strict check.

  numeric    (years)
      The digits must appear in the field's own evidence span. "2025" is
      easy to find somewhere in a CV, so a document-wide search would pass
      almost anything; checking the cited span is the real test.

  classification  (highest_degree, phd_status, position category,
                   publication status)
      These are the model's JOB - mapping prose onto our vocabulary. The
      string "phd_pursuing" will never appear in a CV, and demanding it
      would flag every correct answer. So we validate the EVIDENCE instead:
      the span must be real, and for publication status a marker must
      actually be present in it (see the status guard below).

THE STATUS GUARD (required after Stage 3's measured failure)
  Stage 3 showed the model assigns "published" to entries that carry no
  status marker at all - 18 of 19 on Candidate_04, 2 of 16 on Candidate_02.
  Prompt wording did not stop it. So Python stops it: if an entry claims a
  status but its evidence contains no recognised marker, the status is
  downgraded to "unknown" and the change is logged. This enforces the
  project rule that silence must never be read as "published", because
  doing so would reward omitting a status tag over honestly declaring one.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402  (local spec module)
import roles   # noqa: E402  the shared role taxonomy
import education_parse  # noqa: E402  rule-based reading of degree lines
import research_profiles  # noqa: E402  ORCID / Scholar / Scopus links

# How close a string must be to count as "present in the source".
# 1.0 would fail on a single stray space; this tolerates whitespace and
# punctuation noise while still catching invented content.
FUZZY_THRESHOLD = 0.90

FREE_TEXT_FIELDS = (
    "full_name", "email", "orcid", "scholar_url",
    "phd_institution", "phd_subject",
    "masters_degree", "masters_institution", "masters_subject",
    "bachelors_degree", "bachelors_institution", "bachelors_subject",
    "research_area",
)
NUMERIC_FIELDS = ("phd_start_year", "phd_year", "masters_year",
                  "bachelors_year")
CLASSIFICATION_FIELDS = ("highest_degree", "phd_status")
DEGREE_NAME_FIELDS = {"masters_degree": "masters",
                      "bachelors_degree": "bachelors"}
ELIGIBILITY_FLAG_FIELDS = ("net_qualified",)

# A NET/SET claim is the only extracted field that can single-handedly
# satisfy the eligibility gate, so it gets its own guard rather than
# trusting the prompt. The examinations that count:
NET_MARKER = re.compile(
    "(ugc *net|csir *net|ugc-net|csir-net|(^| )net( |$)|slet|(^| )set( |$))",
    re.I,
)
# ...and the one that must NEVER count. GATE is the Graduate Aptitude Test
# in Engineering, an entrance examination conferring no teaching
# eligibility. Candidate_01's CV lists UGC NET and GATE side by side, two
# lines apart, so a model that grabs the wrong line would manufacture
# eligibility. Python checks; the prompt only asks.
NON_QUALIFYING_EXAM = re.compile("(gate|jee|jam|gre|gmat)", re.I)

# Markers that legitimise a non-"unknown" publication status.
STATUS_MARKER = re.compile(
    "|".join(re.escape(k) for k in sorted(schema.STATUS_SURFACE_FORMS, key=len, reverse=True)),
    re.I,
)

# Values that assert an ABSENCE. These cannot carry an evidence span and
# are verified by confirming the CV is silent on the subject instead.
ABSENCE_VALUES = {"none", "no", "not stated"}
DOCTORATE_HINT = re.compile(
    # No backslash escapes needed: source_norm is already lowercase
    # alphanumerics separated by single spaces, so explicit space/anchor
    # alternatives do the job of a word boundary.
    "(^| )(ph ?d|doctor of philosophy|doctorate|d ?phil)( |$)"
)

# --- Doctorate cross-check -------------------------------------------------
# The highest-stakes field in the schema, and the one the model gets wrong
# in the most dangerous direction. Candidate_02's education row reads
# "2020  -  Ph.D.  Indian Institute of Information Technology  7.5 CGPA":
# an OPEN-ENDED range with nothing after the dash. One run read it as
# "pursuing" (correct); a later run read 2020 as the award year and
# returned "awarded", which would have flipped a mid-PhD candidate to
# ELIGIBLE. The prompt already forbids this and the prompt did not hold.
#
# So Python cross-checks the claim against the CV. The guard only ever
# moves an answer toward "needs a human" - it can withhold an unsupported
# "awarded", never manufacture one.
#
# WHAT IT MUST READ, AND WHAT IT MUST NOT. The check originally searched
# the WHOLE document for these words, and that was wrong in a way no test
# caught: Candidate_08's CV supervises three doctoral students, so the
# word "ongoing" appears under "Research Student Supervision (Ph.D.)".
# That word - about his STUDENTS - withheld his OWN doctorate, awarded in
# 2022, costing 15 marks and routing a qualified professor to review.
# Every candidate senior enough to supervise would have hit it.
#
# These are ordinary CV words. "Ongoing" describes projects, "Present"
# ends an employment row, "pursuing" belongs to a student being
# supervised. They mean "this degree is unfinished" ONLY inside the
# candidate's own doctorate entry, which is why they are matched against
# doctorate_context() and never against the document. The same reasoning
# is already written out in cross_check_thesis_submitted below; it simply
# was never applied here.

# Matched against NORMALISED text, so a space or the string edge stands in
# for a word boundary. Without one, "present" fired inside "presented" and
# "representative" - ordinary words in a publication line below a degree.
PHD_IN_PROGRESS_HINT = re.compile(
    "(^| )(research scholar|phd scholar|ph d scholar|doctoral scholar|"
    "pursuing|perusing|ongoing|in progress|continuing|"
    "thesis submitted|under submission|under evaluation|under examination|"
    "present|till date|to date|expected|anticipated|awaiting|not yet)( |$)"
)

# A doctorate awarded within two years of starting is not credible; it
# almost always means a start year was misread as a completion year.
MIN_PLAUSIBLE_PHD_YEARS = 2

# The rows that quote the candidate's own doctorate. Their evidence spans
# are always in scope: a stage printed inside the row is the most direct
# evidence of it there is.
DOCTORATE_ROW_FIELDS = ("phd_status", "highest_degree", "phd_subject",
                        "phd_institution", "phd_start_year", "phd_year")

# How many lines after the degree line still belong to the same entry.
#
# SCOPE IS MEASURED IN LINES, NOT CHARACTERS. A character window was tried
# first and is not safe at any size: it has no idea where the entry ends,
# so on a densely-set CV 240 characters runs straight out of the education
# block and into the next section, while on a sparse one it stops halfway
# through the institution name. A CV entry is a small group of lines that
# ends at a blank line or the next heading, and reading it that way is the
# only version that behaves the same on both layouts.
#
# Nothing BEFORE the degree line is read. Dates printed above it arrive
# anyway through the phd_start_year / phd_year evidence spans, and
# stepping backwards risks picking up the previous entry - an employment
# row ending "2023 - Till Date" would otherwise read as an unfinished
# degree.
DOCTORATE_CONTEXT_LINES = 4

# Where one CV entry stops and the next begins. Matched against a
# normalised line of at most SECTION_HEADING_WORDS words, so an ordinary
# sentence that happens to open with one of these words is not mistaken
# for a heading.
# Each stem ends in \w*, because CV headings are almost all plural
# ("Projects", "Publications", "Activities") and a \b here matched none of
# them. The looseness is safe: _ends_the_entry() exempts any line carrying
# a stage word before it ever asks whether the line is a heading, so the
# worst a stem can do is end the entry early, which narrows scope rather
# than widening it.
SECTION_HEADING = re.compile(
    r"^(education|qualification|academic|experience|employment|appointment|"
    r"position|publication|journal|conference|book|chapter|patent|project|"
    r"research|supervision|student|teaching|award|achievement|honour|honor|"
    r"workshop|seminar|training|certification|membership|skill|reference|"
    r"funding|grant|scholarship|fellowship|editorial|review|personal|"
    r"declaration|interest|profile|summary|objective|highlight|activit|"
    r"responsibilit|contact|language|hobb|course)\w*")
SECTION_HEADING_WORDS = 8

# Sections that describe OTHER PEOPLE. Matched anywhere in a heading, so
# "Research Student Supervision (Ph.D.)" is caught by "student" even
# though the heading opens with "Research".
OTHER_PEOPLE_SECTION = re.compile(
    r"(^| )(reference|referee|supervision|supervised|supervisor|student|"
    r"examiner|committee|collaborat|acknowledg|testimonial|mentee)\w*")


def mentions_own_doctorate(source_text: str) -> bool:
    """
    Does the CV name a doctorate that could be the CANDIDATE'S own?

    This backs the absence check: "phd_status: none" is a negative claim,
    and no quotation can evidence an absence, so it is confirmed instead
    by establishing that the CV really is silent about a doctorate.

    Silence has to be judged over the candidate's OWN sections. An
    academic CV names referees, doctoral students, examiners and
    committee members, and every one of those carries somebody else's
    PhD. Searching the whole document found a referee's doctorate on a
    master's-only candidate and reported "doctorate evidenced but its
    award is not confirmed" - sending a reviewer to look for a degree
    that does not exist. Same scope error as cross_check_doctorate, in
    the other direction.

    The safety net it exists for is untouched: a doctorate printed under
    Education still contradicts a "none" claim and still routes to a
    human.
    """
    in_other_people = False
    for raw in (source_text or "").splitlines():
        line = normalise(raw)
        if not line.strip():
            continue
        if len(line.split()) <= SECTION_HEADING_WORDS and \
                SECTION_HEADING.match(line):
            # Set before the mention check below, so a heading that names
            # a doctorate itself is judged by its own section.
            in_other_people = bool(OTHER_PEOPLE_SECTION.search(line))
        if not in_other_people and DOCTORATE_HINT.search(line):
            return True
    return False


COMPLETION_YEAR_FIELDS = ("bachelors_year", "masters_year", "phd_year")
YEAR_IN_TEXT = re.compile(r"\b(19|20)\d{2}\b")


def cross_check_completion_years(results: list[dict]) -> list[str]:
    """
    A completion year cannot be the START of its own date range.

    Candidate_01's masters_year came back 2023 with the evidence span
    "Aug 2023 - July 2025" - the model took the first year it saw. Every
    correctly-read case in the sample takes the later year (2025, 2014,
    2021), so the rule is not a guess about this CV, it is the definition
    of a completion year.

    Fires ONLY when the evidence contains exactly two distinct years and
    the extracted value is the earlier of them. An open-ended row like
    "2020 - Ph.D." has one year and is left entirely alone - that case
    belongs to cross_check_doctorate, which withholds rather than
    corrects.
    """
    notes = []
    by_field = {r["field"]: r for r in results}

    for field_name in COMPLETION_YEAR_FIELDS:
        row = by_field.get(field_name)
        if not row or not isinstance(row.get("value"), int):
            continue

        years = sorted({int(m.group(0))
                        for m in YEAR_IN_TEXT.finditer(row.get("evidence") or "")})
        if len(years) != 2:
            continue

        earlier, later = years
        if row["value"] == earlier:
            row["value"] = later
            row["corrected_from"] = earlier
            note = (f"{field_name}: evidence span '{row.get('evidence', '')[:48]}' "
                    f"is a range {earlier}-{later}; the extracted value was the "
                    f"START year. Corrected to the completion year {later}.")
            row["detail"] = note
            notes.append(note)

    return notes


def cross_check_thesis_submitted(results: list[dict]) -> list[str]:
    """
    Recognise a submitted thesis from the doctorate row's OWN evidence.

    The extractor has no reliable slot for this stage and returns
    "pursuing" (or, worse, "awarded") for a row that reads
    "Ph.D. (Thesis Submitted)". The stage is stated in the CV's own words,
    so Python reads it there - the same division of labour as the
    completion-year correction above.

    SCOPE IS THE ROW'S EVIDENCE SPAN, NOT THE WHOLE CV. A CV can contain
    "thesis submitted" about someone else entirely - a student the
    candidate supervised, a referee's note - and searching the whole text
    would credit the candidate for it.

    DIRECTION. Moves "pursuing" or a missing value UP to thesis_submitted,
    and moves "awarded" DOWN to it. The downgrade matters more: a model
    reading "Ph.D. (Thesis Submitted) 2021 - 2026" as awarded in 2026 has
    manufactured a qualification, and that is the error this project
    treats as the most dangerous it can make. "none" is left alone - a
    negative claim is handled by its own absence check.
    """
    notes = []
    by_field = {r["field"]: r for r in results}
    markers = schema.THESIS_SUBMITTED_MARKERS

    def states_submission(row) -> str | None:
        text = re.sub(r"\s+", " ", (row or {}).get("evidence") or "").lower()
        return next((m for m in markers if m in text), None)

    def traceable(row) -> bool:
        # Only correct a row whose evidence span was actually found in the
        # CV. A span that failed traceability is unproven text, and
        # reading a stage out of it would launder a hallucination into a
        # correction.
        return (row or {}).get("status") in ("verified", "partial")

    status_row = by_field.get("phd_status")
    marker = states_submission(status_row)
    if status_row and marker and traceable(status_row) and \
            status_row.get("value") in ("pursuing", "awarded", None, ""):
        before = status_row.get("value")
        status_row["value"] = "thesis_submitted"
        status_row["corrected_from"] = before
        note = (f"phd_status: the doctorate row reads {marker!r}, so the "
                f"stage is 'thesis submitted' rather than {before!r}. The "
                f"award is NOT treated as conferred.")
        status_row["detail"] = note
        notes.append(note)

        # An award year cannot coexist with an unconferred award; the end
        # of an enrolment range is not one.
        year_row = by_field.get("phd_year")
        if year_row and year_row.get("value") is not None:
            year_row["corrected_from"] = year_row["value"]
            year_row["value"] = None
            year_row["detail"] = ("cleared: the thesis is submitted, so no "
                                  "award year exists yet")

    degree_row = by_field.get("highest_degree")
    if degree_row and states_submission(degree_row) and traceable(degree_row) \
            and degree_row.get("value") in ("phd", "phd_pursuing"):
        degree_row["corrected_from"] = degree_row["value"]
        degree_row["value"] = "phd_submitted"
        degree_row["detail"] = ("highest_degree: the row states the thesis "
                                "is submitted, not that the degree is held")

    return notes


# ---------------------------------------------------------------------------
# Reconciling the model's degree fields with the CV's own degree lines
# ---------------------------------------------------------------------------
# The model's reading of qualifications is checked against education_parse,
# which reads the degree lines directly. Where the model left a field
# empty, or gave a value that failed traceability, the parser's value is
# used - quoted from the CV, with the degree line as its evidence and the
# reason recorded. Where the model's value is traceable it is KEPT; the
# parser fills gaps, it does not overrule good reads.
#
# One correction runs in the other direction on purpose. The model
# repeatedly called a doctorate whose own entry closes in a past year
# "pursuing" - "September 2020 - September 2024", "Year of Passing 2023" -
# and an unfinished doctorate scores zero. The parser concludes completion
# from the entry itself (schema: no keyword such as "awarded" is
# required), so a "pursuing" contradicted by a closed, past period with no
# in-progress wording is corrected to "awarded". The reverse - an
# "awarded" contradicted by in-progress wording in the entry - withholds
# the claim, as cross_check_doctorate always has.

EDUCATION_PREFIX = {"doctorate": "phd", "masters": "masters",
                    "bachelors": "bachelors"}
_USABLE = ("verified", "partial")


def reconcile_education(results: list[dict], source_text: str,
                        as_of=None) -> tuple[list[str], list[str]]:
    """
    Fill and correct degree fields from the CV's degree lines.

    Returns (notes, contradictions). Contradictions are claims of an
    award that the CV's own entry contradicts; they route to review.
    Mutates `results` in place.
    """
    entries = education_parse.parse_education(source_text, as_of)
    by_field = {r["field"]: r for r in results}
    notes: list[str] = []
    contradictions: list[str] = []

    def usable(name: str) -> bool:
        row = by_field.get(name) or {}
        return row.get("status") in _USABLE and \
            row.get("value") not in (None, "", [])

    def fill(name: str, value, entry, why: str, **extra) -> None:
        row = by_field.get(name)
        if row is None:
            row = {"field": name}
            results.append(row)
            by_field[name] = row
        before = row.get("value")
        row.update({"value": value, "evidence": entry.line,
                    "status": "verified", "evidence_in_source": True,
                    "source": "cv_rule_parser", "detail": why, **extra})
        if before not in (None, "", value):
            row["corrected_from"] = before
        notes.append(f"{name}: {why}")

    def entry_for(level: str):
        """The CV entry behind the model's degree, else the best one."""
        pool = [e for e in entries if e.level == level]
        stated = (by_field.get(f"{EDUCATION_PREFIX[level]}_degree") or {})
        if usable(f"{EDUCATION_PREFIX[level]}_degree"):
            name = normalise(str(stated.get("value")))
            same = [e for e in pool if name and name in normalise(e.line)]
            if same:
                return education_parse.best_entry(same, level)
            return pool[0] if len(pool) == 1 else None
        return education_parse.best_entry(entries, level)

    # --- bachelor's and master's -----------------------------------------
    for level in ("masters", "bachelors"):
        entry = entry_for(level)
        if not entry:
            continue
        prefix = EDUCATION_PREFIX[level]
        source = f"the CV's education entry {entry.line[:70]!r}"
        if not usable(f"{prefix}_degree"):
            fill(f"{prefix}_degree", entry.name, entry,
                 f"read from {source}; the extracted value was missing or "
                 f"could not be traced to the CV")
        if not usable(f"{prefix}_institution") and entry.institution:
            fill(f"{prefix}_institution", entry.institution, entry,
                 f"read from {source}")
        if not usable(f"{prefix}_subject") and entry.subject:
            fill(f"{prefix}_subject", entry.subject, entry,
                 f"read from {source}")
        if not usable(f"{prefix}_year") and entry.status == "completed":
            fill(f"{prefix}_year", entry.year, entry,
                 f"{entry.basis} ({source})")

    # --- the doctorate ---------------------------------------------------
    entry = education_parse.best_entry(entries, "doctorate")
    if not entry:
        return notes, contradictions

    source = f"the CV's doctorate entry {entry.line[:70]!r}"
    status_row = by_field.get("phd_status") or {}
    stated = status_row.get("value") if usable("phd_status") else None

    if entry.status == "completed":
        corroboration = education_parse.doctorate_corroboration(source_text)
        extra = {"completion_basis": entry.basis,
                 "corroboration": corroboration,
                 "needs_confirmation": (entry.needs_confirmation
                                        or not corroboration)}
        if stated == "thesis_submitted":
            pass    # the stated stage wins over an inference from dates
        elif stated == "awarded":
            if not usable("phd_year"):
                fill("phd_year", entry.year, entry, f"{entry.basis} ({source})")
        else:
            fill("phd_status", "awarded", entry,
                 f"completed: {entry.basis} ({source})"
                 + (f"; supported by: {'; '.join(corroboration)}"
                    if corroboration else
                    "; no other context in the CV corroborates the "
                    "completion, so please confirm"), **extra)
            fill("phd_year", entry.year, entry, f"{entry.basis} ({source})")

        # A single year in a "Year of Passing" column is not a start year.
        start_row = by_field.get("phd_start_year") or {}
        if entry.single_year and start_row.get("value") == entry.year:
            start_row.update({"corrected_from": entry.year, "value": None,
                              "status": "absent",
                              "detail": (f"cleared: {entry.year} is the year "
                                         f"of passing in {source}, not a "
                                         f"start year")})
            notes.append(f"phd_start_year: {start_row['detail']}")
        elif entry.start and not usable("phd_start_year"):
            fill("phd_start_year", entry.start[0], entry,
                 f"start of the period printed in {source}")

        if not usable("highest_degree") or \
                by_field["highest_degree"].get("value") != "phd":
            fill("highest_degree", "phd", entry,
                 f"the CV's own entry shows a completed doctorate ({source})")

    elif entry.status == "thesis_submitted":
        if stated in (None, "pursuing", "awarded"):
            fill("phd_status", "thesis_submitted", entry,
                 f"{entry.basis} ({source}); the award is NOT treated as "
                 f"conferred")
            year_row = by_field.get("phd_year") or {}
            if year_row.get("value") is not None:
                year_row.update({"corrected_from": year_row["value"],
                                 "value": None, "status": "absent",
                                 "detail": "cleared: the thesis is submitted, "
                                           "so no award year exists yet"})
        if not usable("highest_degree"):
            fill("highest_degree", "phd_submitted", entry,
                 f"{entry.basis} ({source})")

    elif entry.status == "pursuing":
        if stated == "awarded":
            note = (f"doctorate reported as awarded, but {source} reads "
                    f"{entry.markers[0] if entry.markers else 'as unfinished'!r}"
                    f" ({entry.basis}). Award not confirmed - routed to human "
                    f"review.")
            status_row.update({"status": "unverified", "detail": note,
                               "cross_check_failed": True})
            contradictions.append(note)
        elif stated is None:
            fill("phd_status", "pursuing", entry, f"{entry.basis} ({source})")
        if not usable("highest_degree"):
            fill("highest_degree", "phd_pursuing", entry,
                 f"{entry.basis} ({source})")

    if entry.status in ("completed", "thesis_submitted", "pursuing"):
        if not usable("phd_institution") and entry.institution:
            fill("phd_institution", entry.institution, entry,
                 f"read from {source}")
        if not usable("phd_subject") and entry.subject:
            fill("phd_subject", entry.subject, entry, f"read from {source}")

    return notes, contradictions


def _ends_the_entry(line_norm: str) -> bool:
    """A blank line or a section heading closes the entry above it."""
    if not line_norm.strip():
        return True
    # A line carrying a stage word is content, never a heading. Without
    # this, "Research Scholar, 2018 onwards" - the very thing the guard
    # exists to catch - would be discarded as a heading starting
    # "Research".
    if PHD_IN_PROGRESS_HINT.search(line_norm):
        return False
    return (len(line_norm.split()) <= SECTION_HEADING_WORDS
            and bool(SECTION_HEADING.match(line_norm)))


def doctorate_context(results: list[dict], source_text: str) -> str:
    """
    The part of the CV that actually describes THIS candidate's doctorate.

    Two sources, both narrow:
      - the doctorate rows' own evidence spans, always. A stage printed
        inside the quoted row is the most direct evidence there is, and
        "2019 - Present" usually arrives as the phd_start_year span.
      - the degree's own entry in the CV, read forward line by line until
        a blank line or the next heading. That catches a qualifier set on
        the line below the degree without crossing into the next section.

    Returns "" when nothing is traceable. That is the correct answer, not
    a failure: with no anchor there is no evidence about the stage, and an
    untraceable phd_status row is already marked unverified by
    check_scalar. Falling back to the whole document here is exactly the
    bug this function exists to remove.
    """
    by_field = {r["field"]: r for r in results}

    def span(name: str) -> str:
        row = by_field.get(name) or {}
        # Only read a span that was actually found in the CV. Reading a
        # stage out of unproven text would launder a hallucination.
        if row.get("status") not in ("verified", "partial"):
            return ""
        return normalise(row.get("evidence") or "")

    context = [s for s in (span(f) for f in DOCTORATE_ROW_FIELDS) if s]

    lines = [normalise(line) for line in (source_text or "").splitlines()]
    for anchor in ("phd_status", "highest_degree", "phd_subject",
                   "phd_institution"):
        needle = span(anchor)
        if not needle:
            continue
        start = next((i for i, line in enumerate(lines)
                      if line and _locate(needle, line) >= 0), None)
        if start is None:
            continue
        for offset, line in enumerate(lines[start:start
                                            + DOCTORATE_CONTEXT_LINES]):
            if offset and _ends_the_entry(line):
                break
            context.append(line)
        break

    return " ".join(context)


def cross_check_doctorate(results: list[dict], source_text: str) -> list[dict]:
    """
    Withhold an 'awarded' claim the CV does not actually support.

    Returns reviewer notes. Mutates the phd_status / phd_year rows in
    `results` when the claim is contradicted.
    """
    by_field = {r["field"]: r for r in results}
    status_row = by_field.get("phd_status")
    if not status_row or status_row.get("value") != "awarded":
        return []

    reasons = []
    marker = PHD_IN_PROGRESS_HINT.search(doctorate_context(results, source_text))
    if marker:
        reasons.append(f"the doctorate's own entry in the CV reads "
                       f"{marker.group(2)!r}, which describes a degree still "
                       f"in progress")

    start = (by_field.get("phd_start_year") or {}).get("value")
    end = (by_field.get("phd_year") or {}).get("value")
    if isinstance(start, int) and isinstance(end, int):
        if end - start < MIN_PLAUSIBLE_PHD_YEARS:
            reasons.append(f"claimed award year {end} is implausibly close to "
                           f"the start year {start}")

    if not reasons:
        return []

    note = ("doctorate reported as awarded, but " + "; and ".join(reasons)
            + ". Award not confirmed - routed to human review.")

    status_row["status"] = "unverified"
    status_row["detail"] = note
    status_row["cross_check_failed"] = True
    year_row = by_field.get("phd_year")
    if year_row and year_row.get("value") is not None:
        year_row["status"] = "unverified"
        year_row["detail"] = note

    return [note]


# Heuristic only - flags, never deletes. A dissertation is real work but is
# not a publication; Candidate_01's M.Tech dissertation was extracted as one.
THESIS_HINT = re.compile(r"\b(dissertation|thesis)\b", re.I)


# ---------------------------------------------------------------------------
# Text matching
# ---------------------------------------------------------------------------

def normalise(text: str) -> str:
    """
    Fold away the differences that are NOT hallucinations.

    Models reproduce content faithfully but reformat freely: they collapse
    the runs of spaces a PDF table produced, straighten curly quotes, and
    turn en-dashes into hyphens. None of that is invention, so none of it
    should be flagged. Case, accents and punctuation all fold; the WORDS
    are what must survive.
    """
    text = unicodedata.normalize("NFKD", str(text))
    text = text.encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Text laid out in table columns
# ---------------------------------------------------------------------------
# Application-form CVs print publications and qualifications in grids whose
# cells wrap. Extracted as text, one title becomes
#
#     Facial Emotion          Pandey,   Kumar, A.     SN Computer   2022
#     Intensity: A            Science   SCI   Published
#     Fusion Way.
#
# Every word of the title is in the CV, in order, with other columns'
# words between them. A substring or sliding-window test can never match
# that, and every such entry was zeroed as "title could not be found in
# the CV" - genuine papers, several already confirmed by OpenAlex with
# the candidate as author.
#
# So a second test reads the words IN ORDER, allowing other words between
# them within a bounded span. It also repairs the one corruption PDF text
# extraction routinely introduces inside words: a dropped ligature
# ("Di!erences" for "Differences" reads as "di erences").
#
# It is a fallback, never the first test, and it is bounded so a title
# cannot be assembled from words scattered across a page: the span is a
# fixed multiple of the title's length, and only values with enough
# distinctive words are eligible.

_STOPWORDS = frozenset(
    "a an the of and in on for to with by at from using its is or as via "
    "into based towards toward".split())
_LIGATURES = ("", "ff", "fi", "fl", "ffi", "ffl")
SCATTER_SPAN_PER_WORD = 10
SCATTER_SPAN_BASE = 15
# Reported as the score of a column-scattered match, so the audit shows a
# value was placed by the ordered-words test rather than read verbatim.
SCATTERED_MATCH_SCORE = 0.89


def _token_matches(word: str, tokens: list[str], i: int) -> int:
    """How many source tokens `word` consumes at position i (0 = no match)."""
    tok = tokens[i]
    if tok == word:
        return 1
    if i + 1 < len(tokens) and len(tok) >= 2 and word.startswith(tok):
        nxt = tokens[i + 1]
        for lig in _LIGATURES:
            if tok + lig + nxt == word:
                return 2
    if len(word) >= 6 and abs(len(word) - len(tok)) <= 2 and tok[:1] == word[:1]:
        import difflib
        if difflib.SequenceMatcher(None, word, tok).ratio() >= 0.85:
            return 1
    return 0


def appears_scattered(needle: str, haystack_norm: str,
                      min_content_words: int = 3) -> bool:
    """
    True when the needle's distinctive words occur in order within a
    bounded span of the source, other columns' words allowed between.
    """
    words = normalise(needle).split()
    content = [w for w in words if len(w) >= 3 and w not in _STOPWORDS]
    if len(content) < min_content_words:
        return False
    tokens = haystack_norm.split()
    max_span = SCATTER_SPAN_PER_WORD * len(words) + SCATTER_SPAN_BASE
    for start in range(len(tokens)):
        if not _token_matches(content[0], tokens, start):
            continue
        i, matched = start, 0
        for word in content:
            while i < len(tokens) and i - start <= max_span:
                used = _token_matches(word, tokens, i)
                i += used or 1
                if used:
                    matched += 1
                    break
            else:
                break
        if matched == len(content):
            return True
    return False


def appears_in(needle: str, haystack_norm: str,
               allow_scattered: bool = False,
               min_content_words: int = 3) -> tuple[bool, float]:
    """
    Is `needle` present in the normalised source?

    Exact substring first (the common case). Failing that, slide a window of
    the needle's length over the haystack and take the best similarity - a
    model that drops one word from a long institution name should not be
    branded a fabricator.

    `allow_scattered` adds the table-column test above as a last resort,
    for values that are themselves CV text (titles, institutions) - never
    for evidence quotations, which must be verbatim.
    """
    found, score = _appears_contiguous(needle, haystack_norm)
    if found or not allow_scattered:
        return found, score
    if appears_scattered(needle, haystack_norm, min_content_words):
        return True, SCATTERED_MATCH_SCORE
    return False, score


def _appears_contiguous(needle: str, haystack_norm: str) -> tuple[bool, float]:
    """The substring and sliding-window tests."""
    n = normalise(needle)
    if not n:
        return False, 0.0
    if n in haystack_norm:
        return True, 1.0

    # Whitespace-insensitive retry. Stage 1 deliberately does NOT rejoin
    # hyphenated line breaks, because un-hyphenating is a guess that would
    # corrupt the evidence text. The price shows up here: Candidate_02's CV
    # contains "Commu-\nnication", so the source reads "commu nication"
    # while the model correctly wrote "communication". Comparing with all
    # spaces removed accepts the model's accurate reconstruction without
    # Stage 1 having to guess. Safe because it only ever merges across
    # boundaries that already matched character-for-character otherwise.
    if n.replace(" ", "") in haystack_norm.replace(" ", ""):
        return True, 1.0

    # Cheap pre-filter: if most of the needle's words are missing, stop.
    words = n.split()
    present = sum(1 for w in words if w in haystack_norm)
    coverage = present / len(words)
    if coverage < 0.6:
        return False, round(coverage, 2)

    import difflib
    best = 0.0
    step = max(1, len(n) // 4)
    for start in range(0, max(1, len(haystack_norm) - len(n) + 1), step):
        window = haystack_norm[start:start + len(n)]
        ratio = difflib.SequenceMatcher(None, n, window).ratio()
        if ratio > best:
            best = ratio
            if best >= FUZZY_THRESHOLD:
                break
    return best >= FUZZY_THRESHOLD, round(best, 2)


# ---------------------------------------------------------------------------
# Field-level validation
# ---------------------------------------------------------------------------

def check_scalar(name: str, wrapper, source_norm: str,
                 own_doctorate_mention: bool | None = None) -> dict:
    """Validate one {"value", "evidence"} pair."""
    if not isinstance(wrapper, dict):
        return {"field": name, "status": "malformed", "value": wrapper,
                "detail": "expected {'value','evidence'}"}

    value, evidence = wrapper.get("value"), wrapper.get("evidence") or ""

    if value in (None, "", []):
        return {"field": name, "status": "absent", "value": None, "detail": ""}

    ev_ok, ev_score = (appears_in(evidence, source_norm) if evidence.strip()
                       else (False, 0.0))

    result = {"field": name, "value": value, "evidence": evidence,
              "evidence_in_source": ev_ok, "evidence_score": ev_score}

    if name in ELIGIBILITY_FLAG_FIELDS:
        # Only a positive claim needs checking; null means "not stated".
        if value is not True:
            result["status"] = "verified"
            result["detail"] = "no NET/SET claim made"
            return result

        has_net = NET_MARKER.search(evidence) is not None
        only_other_exam = (not has_net
                           and NON_QUALIFYING_EXAM.search(evidence) is not None)

        if not ev_ok:
            result["status"] = "unverified"
            result["detail"] = "evidence span not found in the CV"
        elif only_other_exam:
            result["status"] = "unverified"
            result["detail"] = (
                "evidence cites a non-qualifying examination (GATE/JEE/JAM "
                "confer no teaching eligibility), not NET/SET"
            )
        elif not has_net:
            result["status"] = "unverified"
            result["detail"] = "evidence names no NET/SET qualification"
        else:
            result["status"] = "verified"
            result["detail"] = "NET/SET qualification evidenced in the CV"
        return result

    if name in CLASSIFICATION_FIELDS:
        # A NEGATIVE claim cannot have a positive evidence span. "phd_status:
        # none" asserts that something is absent, and no quotation can show
        # an absence - demanding one rejected Candidate_01's correct answer.
        # Such claims are verified the only way they can be: by confirming
        # the CV really is silent on the subject.
        if str(value).lower() in ABSENCE_VALUES:
            # Scoped to the candidate's own sections - see
            # mentions_own_doctorate. The fallback keeps this function
            # usable on its own, where there is no line structure to read.
            mentioned = (own_doctorate_mention
                         if own_doctorate_mention is not None
                         else DOCTORATE_HINT.search(source_norm) is not None)
            result["status"] = "unverified" if mentioned else "verified"
            result["detail"] = (
                "claims no doctorate, but the CV does mention one"
                if mentioned else
                "verified by absence: the CV contains no doctorate mention"
            )
            result["verified_by_absence"] = not mentioned
            return result

        result["status"] = "verified" if ev_ok else "unverified"
        result["detail"] = ("classification supported by a real evidence span"
                            if ev_ok else "evidence span not found in the CV")
        return result

    if name in NUMERIC_FIELDS:
        digits = re.findall(r"\d{4}", str(value))
        in_evidence = all(d in evidence for d in digits) if digits else False
        in_source = all(d in source_norm for d in digits) if digits else False

        if ev_ok and in_evidence:
            result["status"] = "verified"
            result["detail"] = ""
        elif in_evidence and in_source and ev_score >= 0.6:
            # The model quoted a COMPRESSED span - real text with the middle
            # dropped (Candidate_04: "M. Sc. (Physics) ... 2012 - 2014",
            # omitting the institution that sits between). The number is
            # traceable to the CV, so discarding it would lose a correct
            # fact; but the span is not verbatim, so that defect is recorded
            # rather than ignored.
            result["status"] = "partial"
            result["detail"] = (f"value traceable to the CV, but the quoted "
                                f"evidence is not verbatim (score {ev_score})")
        else:
            result["status"] = "unverified"
            result["detail"] = f"{value} not traceable to its evidence span"
        return result

    # free_text
    # Institutions and subjects are CV text that a table may split across
    # columns; two distinctive words in order are enough to place them.
    scattered = name.endswith(("_institution", "_subject"))
    val_ok, val_score = appears_in(value, source_norm,
                                   allow_scattered=scattered,
                                   min_content_words=2)
    result["value_in_source"] = val_ok
    result["value_score"] = val_score

    # A degree NAME the extractor normalised ("btech" for a CV that prints
    # "B.E.") or replaced with the vocabulary word ("masters") is not in
    # the CV, but the qualification it stands for is - in the very span
    # it quoted. Reading the name from that span recovers the real value
    # without inventing one. This used to happen only at scoring time, by
    # which point this function had already deleted the span it needed.
    if not val_ok and name in DEGREE_NAME_FIELDS and ev_ok:
        token = education_parse.find_degree_token(evidence,
                                                  DEGREE_NAME_FIELDS[name])
        if token:
            result.update({
                "value": token, "corrected_from": value,
                "status": "verified", "value_in_source": True,
                "detail": (f"the extractor returned {value!r}, which the CV "
                           f"does not print; the qualification named in its "
                           f"own quoted span is {token!r}, so that is used"),
            })
            return result

    result["status"] = "verified" if val_ok else "unverified"
    result["detail"] = ("" if val_ok
                        else f"value not found in the CV (best match {val_score})")
    if val_ok and val_score == SCATTERED_MATCH_SCORE:
        result["detail"] = ("placed in the CV word by word across table "
                            "columns rather than as one run of text")
    return result


# How far either side of a title to look for its author list. A citation
# that wraps over three or four lines stays well inside this.
AUTHOR_WINDOW = 500


def _locate(needle_norm: str, haystack_norm: str) -> int:
    """
    Index of `needle_norm` in `haystack_norm`, tolerating the space that a
    PDF line break leaves inside a word ("commu nication").

    Falls back to stripping spaces from both and mapping the hit back to a
    position in the original string, so the caller can still take a window
    around it.
    """
    if not needle_norm:
        return -1
    direct = haystack_norm.find(needle_norm)
    if direct >= 0:
        return direct

    # position in the space-stripped string -> position in the original
    index_map = [i for i, ch in enumerate(haystack_norm) if ch != " "]
    stripped = haystack_norm.replace(" ", "")
    hit = stripped.find(needle_norm.replace(" ", ""))
    return index_map[hit] if hit >= 0 and hit < len(index_map) else -1


def _unsupported_authors(authors: list, title: str, evidence: str,
                         source_norm: str) -> list:
    """
    Return the authors that do NOT appear near their paper in the CV.

    Falls back to the evidence span when the title cannot be located, so a
    title that failed its own check does not also produce a cascade of
    author complaints about the same underlying problem.
    """
    position = _locate(normalise(title), source_norm) if title else -1

    if position >= 0:
        start = max(0, position - AUTHOR_WINDOW)
        haystack = source_norm[start: position + len(normalise(title)) + AUTHOR_WINDOW]
    else:
        # The title could not be located at all, so there is no neighbourhood
        # to search. Checking the evidence span instead would just re-report
        # the title problem as a bogus author problem, so we stay silent and
        # let the title check speak for itself.
        return []

    return [a for a in authors
            if normalise(a) and normalise(a) not in haystack]


# A page range, a volume, or a DOI. Each is a locator that only exists
# once a work has actually appeared somewhere.
_PAGES = re.compile(r"\bpp?\.?\s*\d+\s*[-‐-―]+\s*\d+", re.I)
_VOLUME = re.compile(r"\bvol(?:ume)?\.?\s*\d+", re.I)
_DOI = re.compile(r"\b10\.\d{4,9}/\S+", re.I)
# "Proceedings of ...", "in 2017 2nd International Conference ..." - a
# named venue in the citation line itself, for CVs that print the venue
# inline rather than in a separate field.
_VENUE_WORDS = re.compile(
    r"\b(journal|transactions|proceedings|conference|symposium|workshop"
    r"|letters|magazine|annual technical volume|lecture notes|springer"
    r"|ieee|acm|elsevier|wiley)\b", re.I)


def _bibliographically_complete(pub: dict, evidence: str) -> tuple[bool, str]:
    """
    Is this entry a specific, checkable claim that the work appeared?

    Requires a venue AND one locator (pages, volume or DOI). Both halves
    matter: a venue alone is an intention ("submitted to Nature"), and a
    locator alone could be anything. Together they are a citation a
    reader could follow, which is what publication means.
    """
    venue = (pub.get("venue") or "").strip()
    haystack = f"{venue} {evidence}"
    has_venue = bool(venue) or bool(_VENUE_WORDS.search(haystack))
    if not has_venue:
        return False, ""

    if pub.get("doi") or _DOI.search(haystack):
        locator = "a DOI"
    elif _PAGES.search(haystack):
        locator = "a page range"
    elif _VOLUME.search(haystack):
        locator = "a volume number"
    else:
        return False, ""

    return True, (
        f"the CV gives a complete citation - a named venue and {locator} - "
        f"which is a specific claim that the work appeared, so it is read "
        f"as published even though no status word is printed beside it")


# The start of the NEXT reference in a list: "10 Mahala, A.", "[3] ...",
# "3. ...", or a bullet. One to three digits only, so a continuation line
# that opens with a page range or a DOI fragment ("30881-30909.",
# "1016/j.cep...") is not mistaken for a new entry.
ENTRY_START = re.compile(r"^\s*(?:\[\d{1,3}\]|\d{1,3}[.)]?|[-*•●▪])\s+\S")
ENTRY_MAX_LINES = 6


def entry_text(title: str, source_text: str) -> str:
    """
    The lines of the CV that make up ONE publication entry.

    Used when the extractor returned no quotation. Without it, the status
    guard and the citation-completeness check had nothing to read and
    concluded the worst: Candidate_08's "...Deep Enhancement Approach for
    Low-Light Images ... Accepted and Presented." was correctly read as
    accepted, then downgraded to "unknown" because the status word was not
    in a quotation that did not exist.

    Read forward from the line where the title begins, stopping at a blank
    line or the start of the next numbered or bulleted entry - so the
    status of the paper BELOW cannot be borrowed by this one. Returns ""
    when the title cannot be placed; the caller then has no evidence
    either way, which is the honest position.
    """
    if not title or not source_text:
        return ""
    lines = source_text.splitlines()
    norm = [normalise(line) for line in lines]
    head = " ".join(normalise(title).split()[:5])
    if not head:
        return ""
    # _locate, not "in": it also matches with spaces removed, which is what
    # a hyphenated PDF line break inside a title needs - "A Sys-/tematic"
    # reads "a sys tematic" once normalised.
    start = next((i for i, line in enumerate(norm)
                  if line and _locate(head, line) >= 0), None)
    if start is None:
        # The opening words straddle a line break.
        start = next((i for i in range(len(norm) - 1)
                      if _locate(head, f"{norm[i]} {norm[i + 1]}") >= 0), None)
    if start is None:
        return ""
    kept = [lines[start]]
    for line in lines[start + 1:start + ENTRY_MAX_LINES]:
        if not line.strip() or ENTRY_START.match(line):
            break
        kept.append(line)
    return " ".join(kept)


def check_publication(index: int, pub: dict, source_norm: str,
                      source_text: str = "") -> dict:
    """Validate one publication entry and apply the status guard."""
    issues: list[str] = []
    flags: list[str] = []
    title = pub.get("title") or ""
    evidence = pub.get("evidence") or ""

    # Titles may be split across the columns of a publication table.
    title_ok, title_score = (appears_in(title, source_norm, allow_scattered=True)
                             if title else (False, 0.0))
    has_span = bool(evidence.strip())
    ev_ok, ev_score = (appears_in(evidence, source_norm) if has_span
                       else (False, 0.0))

    # TRACEABILITY IS DECIDED BY THE TITLE, AND ONLY BY THE TITLE.
    #
    # This block used to fail an entry whenever the evidence span was not
    # found - including when the extractor never returned one. The message
    # said "evidence span not found in CV", which was not true: nothing had
    # been looked for. Thirty-three of Candidate_08's forty-two
    # publications came back with evidence: null, and every one was
    # written off, although each title appeared in the CV word for word at
    # similarity 1.00. Twenty-five of them had already been confirmed
    # against OpenAlex, title AND author, several with twenty-odd
    # citations. The strongest evidence the system can obtain was being
    # overruled by the weakest: whether the model happened to echo a quote.
    #
    # It also contradicted the written policy. schema.CREDIT_EXCLUSIONS
    # defines failed_validation as "its title could not be found in the
    # CV" - the title, not the quotation. The code now does what the
    # policy says.
    #
    # The question Stage 4 exists to answer is whether this entry comes
    # from the CV. A title found verbatim answers it. The span is
    # corroboration, so its ABSENCE is an omission by the extractor, while
    # a span that was supplied and cannot be found is a different thing
    # entirely - a sign the text may have been invented - and is kept
    # apart below rather than lumped in with it.
    if not title:
        issues.append("the entry has no title, so nothing can be traced "
                      "to the CV")
    elif not title_ok:
        issues.append(f"title not found in CV (best {title_score})")

    if has_span and not ev_ok:
        flags.append(
            f"the quoted citation line could not be found in the CV (best "
            f"match {ev_score}). The title itself was found, so the entry is "
            f"kept and scored; the quotation needs a reviewer's eye in case "
            f"the extractor invented it.")
    elif not has_span:
        flags.append(
            "the extractor returned no citation line for this entry. The "
            "title was matched against the CV directly, so traceability "
            "does not depend on the quotation.")

    # Authors are NOTED here, never used to fail validation.
    #
    # Stage 4's job is traceability: does this text come from the CV? Who
    # actually wrote a paper is Stage 5's question, answered against an
    # external index. Conflating the two produced a flat contradiction on
    # Candidate_06: OpenAlex returned an EXACT title match with her name
    # in the author list, while this check marked the same entry
    # "unverified" and excluded it from credit - because her CV lists
    # publications by title only, with no author lines, so her name
    # appears in the header and nowhere near the titles.
    #
    # Listing one's own papers without repeating one's own name is the
    # normal way to write a CV. Treating it as an integrity failure
    # punished an ordinary formatting choice and overrode real external
    # evidence with a local heuristic.
    unsupported = _unsupported_authors(
        pub.get("authors") or [], title, evidence, source_norm
    )
    if unsupported:
        flags.append(
            f"co-author(s) named in the extraction do not appear near this "
            f"title in the CV: {', '.join(unsupported[:3])}. Informational "
            f"only - authorship is assessed against the index in Stage 5, "
            f"not from CV layout.")

    # --- the status guard -------------------------------------------------
    # Normalise FIRST. The extractor returns whatever word the CV used,
    # and Stage 6 used to read it with STATUS_CLASS.get(status, "unknown")
    # - which handed every unrecognised word the one class that has a
    # full-credit promotion path. "[Communicated]" fell through exactly
    # that way. See schema.normalise_status.
    raw_status = pub.get("status")
    declared, recognised = schema.normalise_status(raw_status)
    if not recognised:
        flags.append(
            f"the CV states a publication status this rubric does not "
            f"recognise ({str(raw_status)[:40]!r}). It is treated as "
            f"unstated and needs a reviewer's eye - it has NOT been read "
            f"as a claim that the work is published.")

    # What the status word and citation details are read from: the
    # quotation when there is one, otherwise the entry's own lines. An
    # absent quotation is the extractor's omission and must not be read as
    # the CV saying nothing.
    entry = evidence if has_span else entry_text(title, source_text)

    guard = None
    if has_span:
        corroborated = bool(STATUS_MARKER.search(evidence))
    else:
        # Stricter than the quotation case: the word found in the entry
        # must state the SAME status, not merely some status.
        corroborated = any(
            schema.normalise_status(m.group(0))[0] == declared
            for m in STATUS_MARKER.finditer(entry))
    if declared != "unknown" and not corroborated:
        guard = {"from": declared, "to": "unknown",
                 "reason": ("no status marker present in the evidence span"
                            if has_span else
                            "the CV entry does not state this status")}
        declared = "unknown"

    # --- a complete citation is itself a publication claim ----------------
    # Promotes "unknown" only, and never an entry the candidate has
    # labelled as still under review. See schema.BIBLIOGRAPHIC_COMPLETENESS.
    citation = None
    if declared == "unknown" and recognised:
        complete, why = _bibliographically_complete(pub, entry)
        if complete:
            citation = {"from": "unknown", "to": "published", "reason": why}
            declared = "published"

    if THESIS_HINT.search(evidence) or THESIS_HINT.search(title):
        flags.append("evidence mentions a thesis/dissertation - may not be a "
                     "publication (reviewer judgement)")

    return {
        "index": index,
        "title": title,
        "status_final": declared,
        "status_downgraded": guard,
        "status_from_citation": citation,
        "status_recognised": recognised,
        "title_in_source": title_ok,
        "evidence_in_source": ev_ok,
        "evidence_span_supplied": has_span,
        "unsupported_authors": unsupported,
        "issues": issues,
        "flags": flags,
        # Three outcomes, because two could not tell "we could not place
        # this in the CV at all" from "we placed it, but one quotation
        # looks wrong". Only the first is a reason to refuse credit.
        #   verified     - title traceable; any supplied span checks out
        #   needs_review - title traceable, supplied span did not match
        #   unverified   - title not traceable: the one veto, and the
        #                  only value verify_pubs turns into
        #                  failed_validation
        "verdict": ("unverified" if issues
                    else "needs_review" if (has_span and not ev_ok)
                    else "verified"),
    }


# ---------------------------------------------------------------------------
# Record-level validation
# ---------------------------------------------------------------------------

# --- Position category review ----------------------------------------------
# The model proposes a category; Python checks it against the definitions
# in schema.py. Same division of labour as the publication status guard.
#
# Candidate_06's "Tech Mentor Intern at Kalvium" came back as "other", so
# it was filtered out of the industry criterion entirely and that
# criterion reported "no industry position" for a CV that plainly lists
# one. The schema's own definition is unambiguous - industry is "any
# non-academic employer" - so this is a rule the code can apply itself
# rather than hope for.

def review_position_categories(positions: list) -> tuple[list, list]:
    """
    Re-derive each position's category from the shared taxonomy.

    The vocabulary lives in roles.py, so Stage 4 and Stage 6 cannot read
    the same title two different ways.

    THE TITLE LEADS, NOT THE EMPLOYER. An earlier version asked the
    employer first and fell back to "any non-academic employer is
    industry", which filed a research post at an institute known only by
    an acronym as industry, and would have done the same to a lectureship
    at a corporate training academy. A role title that names a teaching or
    research post settles the question on its own; the employer is
    consulted only when the title says nothing.

    A category is CHANGED only when the evidence settles it. Where neither
    the title nor the employer is informative, the entry is left as the
    model proposed and FLAGGED for a human, because guessing is what this
    system is built not to do.
    """
    notes = []
    for index, position in enumerate(positions):
        title = position.get("title") or ""
        organisation = position.get("organisation") or ""
        proposed = (position.get("category") or "other").lower()

        verdict = roles.classify_position(position)
        derived = verdict["category"]

        # Low-confidence verdicts never overwrite the model's answer. They
        # only ever add a flag: the classifier saying "I cannot tell" is
        # not a reason to discard what the model read off the page.
        if verdict["confidence"] == "low":
            if verdict["needs_review"]:
                notes.append(
                    f"position[{index}] '{title}'"
                    + (f" at '{organisation}'" if organisation else "")
                    + f": left as {proposed!r} - {verdict['reason']}; "
                      f"please check")
            position["category_review"] = verdict
            continue

        if derived != proposed:
            position["category"] = derived
            position["category_proposed_by_model"] = proposed
            position["category_rule"] = verdict["reason"]
            notes.append(
                f"position[{index}] '{title}'"
                + (f" at '{organisation}'" if organisation else "")
                + f": category {proposed} -> {derived} ({verdict['reason']})")

        position["category_review"] = verdict
        if verdict["needs_review"]:
            notes.append(
                f"position[{index}] '{title}': {verdict['reason']}; "
                f"please check")

    return positions, notes


def check_positions(positions: list, source_norm: str) -> tuple[list, list]:
    """
    Validate each position and carry it through.

    An unsupported ORGANISATION is stripped to null rather than discarding
    the whole role: the role itself may be perfectly real while the
    employer was misread (the model has put a city, and a course name, in
    that field). Losing the dates because the employer was wrong would
    cost the candidate experience they actually have.
    """
    cleaned, issues = [], []

    for index, position in enumerate(positions):
        if not isinstance(position, dict):
            continue
        entry = dict(position)
        title = position.get("title") or ""
        organisation = position.get("organisation")
        evidence = position.get("evidence") or ""

        title_ok, title_score = (appears_in(title, source_norm,
                                            allow_scattered=True,
                                            min_content_words=2)
                                 if title else (False, 0.0))
        if not title_ok:
            issues.append(f"position[{index}] title {title!r} not found in CV "
                          f"(best {title_score})")
            entry["title_unverified"] = True

        if organisation:
            org_ok, org_score = appears_in(organisation, source_norm,
                                           allow_scattered=True,
                                           min_content_words=2)
            if not org_ok:
                issues.append(f"position[{index}] organisation {organisation!r} "
                              f"not found in CV (best {org_score})")
                entry["organisation"] = None
                entry["rejected_organisation"] = organisation

        if evidence and not appears_in(evidence, source_norm)[0]:
            entry["evidence_unverified"] = True

        cleaned.append(entry)

    return cleaned, issues


_PLACEHOLDER = re.compile(r"^\s*(n\s*/?\s*a\.?|nil|none|not applicable|-+)?\s*$",
                          re.I)


def _is_placeholder(value) -> bool:
    """Empty, or an application form's 'NA' / 'Nil' / '-' filler."""
    return value is None or bool(_PLACEHOLDER.match(str(value)))


def _patent_key(patent: dict) -> str:
    """
    Identity of an invention, for duplicate detection.

    The number is the real identifier when present. Falling back to the
    normalised title catches the common CV pattern of listing the same
    invention twice - once when filed and again when granted - without a
    number on either line.
    """
    number = re.sub(r"[^a-z0-9]", "", str(patent.get("number") or "").lower())
    return number or normalise(patent.get("title") or "")


def check_patents(patents: list, source_norm: str) -> list[dict]:
    """
    Validate patent entries: traceable to the CV, identifiable, unique.

    Applies schema.PATENT_EXCLUSIONS. Nothing is deleted - an excluded
    entry stays visible with its reason, because a reviewer needs to see
    the claim AND why it earned nothing.

    Where a filing and its grant are both listed, the HIGHER status is
    kept and the other marked duplicate: the invention progressed, it did
    not happen twice.
    """
    seen: dict[str, int] = {}
    results: list[dict] = []
    rank = {"granted": 3, "published": 2, "filed": 1, "unknown": 0}

    for index, patent in enumerate(patents):
        if not isinstance(patent, dict):
            continue
        title = patent.get("title") or ""
        evidence = patent.get("evidence") or ""
        # An application form's empty row ("1   NA   NA   NA") is the CV
        # saying there is NO patent. Listing it as an excluded patent told
        # the reviewer a claim had been made and rejected.
        if _is_placeholder(title) and _is_placeholder(patent.get("number")):
            continue
        status = (patent.get("status") or "unknown").lower()
        if status not in schema.PATENT_STATUSES:
            status = "unknown"

        exclusions: list[str] = []
        issues: list[str] = []

        title_ok, title_score = (appears_in(title, source_norm,
                                            allow_scattered=True) if title
                                 else (False, 0.0))
        if not title_ok:
            issues.append(f"title not found in CV (best {title_score})")
            exclusions.append("failed_validation")

        if evidence.strip() and not appears_in(evidence, source_norm)[0]:
            issues.append("evidence span not found in CV")

        if not str(patent.get("number") or "").strip():
            exclusions.append("no_identifier")

        key = _patent_key(patent)
        duplicate_of = None
        if key and key in seen:
            previous = results[seen[key]]
            if rank[status] > rank[previous["status_final"]]:
                # This entry supersedes the earlier one.
                previous["credit_exclusions"] = sorted(
                    set(previous["credit_exclusions"]) | {"duplicate_patent"})
                previous["duplicate_of"] = index
                seen[key] = len(results)
            else:
                exclusions.append("duplicate_patent")
                duplicate_of = seen[key]
        elif key:
            seen[key] = len(results)

        results.append({
            "index": index,
            "title": title,
            "number": patent.get("number"),
            "status_final": status,
            "inventors": patent.get("inventors") or [],
            "year": patent.get("year"),
            "jurisdiction": patent.get("jurisdiction"),
            "evidence": evidence,
            "title_in_source": title_ok,
            "issues": issues,
            "credit_exclusions": sorted(set(exclusions)),
            "duplicate_of": duplicate_of,
            # No register was consulted - see schema.PATENT_VERIFICATION.
            "verification": "unavailable",
            "verification_note": schema.PATENT_VERIFICATION["unavailable"],
            "verdict": "verified" if not issues else "unverified",
        })

    return results


def validate_record(record: dict, source_text: str, as_of=None) -> dict:
    """
    Stage 4 for one candidate. `as_of` resolves whether a degree period
    has ended; it defaults to today and run.py passes its pinned date.
    """
    source_norm = normalise(source_text)
    profile = record.get("profile", {})

    own_doctorate = mentions_own_doctorate(source_text)
    scalar_results = [
        check_scalar(name, profile.get(name), source_norm,
                     own_doctorate_mention=own_doctorate)
        for name in (FREE_TEXT_FIELDS + NUMERIC_FIELDS + CLASSIFICATION_FIELDS
                     + ELIGIBILITY_FLAG_FIELDS)
    ]

    # Order matters: correct completion years BEFORE the doctorate
    # cross-check, which reasons about the start/end year gap.
    year_notes = cross_check_completion_years(scalar_results)
    # BEFORE the doctorate cross-check: a row reading "Thesis Submitted"
    # that the model called "awarded" is corrected here, so the check
    # below never sees an award that does not exist. Its notes are kept
    # apart from doctorate_notes, because those set
    # doctorate_cross_check_failed - and recognising a stated stage is not
    # a failed check.
    thesis_notes = cross_check_thesis_submitted(scalar_results)
    # AFTER the model's own spans have been read, BEFORE the award check:
    # the CV's degree lines fill what the model missed and correct a
    # finished doctorate read as "pursuing", and the award check below
    # then tests the result against the entry's own lines.
    education_notes, education_contradictions = reconcile_education(
        scalar_results, source_text, as_of)
    # The RAW text, not source_norm: this check reasons about which lines
    # belong to the doctorate's own entry, and normalising the document
    # collapses the line breaks that mark where that entry ends.
    doctorate_notes = (education_contradictions
                       + cross_check_doctorate(scalar_results, source_text))

    # Build the cleaned profile: unverified values are REMOVED, not kept.
    clean: dict = {}
    for r in scalar_results:
        if r["status"] in ("verified", "partial"):
            clean[r["field"]] = {"value": r["value"],
                                 "evidence": r.get("evidence", "")}
            if r.get("corrected_from") is not None:
                # A corrected value must never look like a clean read.
                clean[r["field"]]["corrected_from"] = r["corrected_from"]
                clean[r["field"]]["correction_note"] = r.get("detail", "")
            if r["status"] == "partial":
                # Kept, but the audit trail must show the span was imperfect.
                clean[r["field"]]["evidence_not_verbatim"] = True
                clean[r["field"]]["note"] = r.get("detail", "")
            if r.get("source") == "cv_rule_parser":
                # Read from the CV's degree line, not the model: say so.
                clean[r["field"]]["source"] = "cv_rule_parser"
                clean[r["field"]]["note"] = r.get("detail", "")
                for key in ("completion_basis", "corroboration",
                            "needs_confirmation"):
                    if key in r:
                        clean[r["field"]][key] = r[key]
        else:
            clean[r["field"]] = {
                "value": None,
                "evidence": "",
                "unverified": True,
                "rejected_value": r.get("value"),
                "reason": r.get("detail", ""),
            }

    # Positions must survive validation, not be dropped. Stage 6 derives
    # teaching_years and industry_years from them, and an earlier version of
    # this function silently omitted them - every candidate then scored zero
    # years of teaching from CVs full of lectureships.
    reviewed_positions, category_notes = review_position_categories(
        profile.get("positions") or [])
    clean_positions, position_issues = check_positions(
        reviewed_positions, source_norm)
    position_issues = position_issues + category_notes

    pub_results = [check_publication(i, p, source_norm, source_text)
                   for i, p in enumerate(record.get("publications", []))]

    clean_pubs = []
    for original, verdict in zip(record.get("publications", []), pub_results):
        entry = dict(original)
        entry["status"] = verdict["status_final"]
        entry["validation"] = {
            "verdict": verdict["verdict"],
            "issues": verdict["issues"],
            "flags": verdict["flags"],
            "status_downgraded": verdict["status_downgraded"],
        }
        clean_pubs.append(entry)

    patent_results = check_patents(record.get("patents", []) or [], source_norm)

    verified = sum(1 for r in scalar_results if r["status"] == "verified")
    partial = sum(1 for r in scalar_results if r["status"] == "partial")
    present = sum(1 for r in scalar_results if r["status"] != "absent")
    downgraded = sum(1 for r in pub_results if r["status_downgraded"])

    clean["positions"] = clean_positions

    return {
        "candidate_id": record.get("candidate_id"),
        "profile": clean,
        "publications": clean_pubs,
        "patents": patent_results,
        "validation_summary": {
            "patents_total": len(patent_results),
            "patents_excluded": sum(1 for p in patent_results
                                    if p["credit_exclusions"]),
            "scalar_fields_present": present,
            "scalar_fields_verified": verified,
            "scalar_fields_partial": partial,
        "scalar_fields_unverified": present - verified - partial,
            "positions_total": len(clean_positions),
        "position_issues": position_issues,
        "publications_total": len(pub_results),
            "publications_with_issues": sum(1 for r in pub_results if r["issues"]),
            "publication_statuses_downgraded": downgraded,
            "flags": ([f for r in pub_results for f in r["flags"]]
                  + doctorate_notes + year_notes + thesis_notes
                  + education_notes),
        "education_from_cv_lines": education_notes,
        "doctorate_cross_check_failed": bool(doctorate_notes),
        },
        "scalar_detail": scalar_results,
        "publication_detail": pub_results,
        # Profile identifiers and self-reported metrics, read by Python from
        # the CV text; Stage 5 decides what can be verified.
        "research_links": research_profiles.detect_links(source_text),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    out_dir = root / "output"

    files = ([Path(a) for a in argv] if argv
             else sorted(out_dir.glob("Candidate_*.extracted.json")))
    if not files:
        print(f"No *.extracted.json in {out_dir}. Run extract_fields.py first.")
        return 1

    print("Stage 4: validation / anti-hallucination\n")

    for path in files:
        stem = path.name.replace(".extracted.json", "")
        source = out_dir / f"{stem}.txt"
        if not source.is_file():
            print(f"{stem}: missing {source.name}")
            continue

        record = json.loads(path.read_text(encoding="utf-8"))
        result = validate_record(record, source.read_text(encoding="utf-8"))
        (out_dir / f"{stem}.validated.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        s = result["validation_summary"]
        print(f"{stem}")
        print(f"  scalars : {s['scalar_fields_verified']}/{s['scalar_fields_present']} verified"
              f", {s['scalar_fields_partial']} partial"
              f", {s['scalar_fields_unverified']} REJECTED")
        print(f"  posns   : {s['positions_total']} carried through"
              + (f", {len(s['position_issues'])} issue(s)" if s['position_issues'] else ""))
        for issue in s["position_issues"]:
            print(f"    POSITION: {issue}")
        print(f"  pubs    : {s['publications_total']} total, "
              f"{s['publications_with_issues']} with issues, "
              f"{s['publication_statuses_downgraded']} status downgraded")

        for note in result["validation_summary"]["flags"]:
            if "doctorate reported as awarded" in note:
                print(f"    CROSS-CHECK: {note}")
        for r in result["scalar_detail"]:
            if r["status"] == "unverified":
                print(f"    REJECTED {r['field']}: {r.get('value')!r} - {r['detail']}")
            elif r["status"] == "partial":
                print(f"    KEPT(partial) {r['field']}: {r.get('value')!r} - {r['detail']}")
        for r in result["publication_detail"]:
            if r["issues"]:
                print(f"    PUB[{r['index']}] {r['title'][:46]!r}")
                for i in r["issues"]:
                    print(f"              - {i}")
            for f in r["flags"]:
                print(f"    FLAG[{r['index']}] {f}")
        print()

    print(f"Wrote *.validated.json to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
