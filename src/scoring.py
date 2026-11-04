"""
STAGE 6 - deterministic scoring.

NO LLM. NO NETWORK. Pure Python over the Stage 4 validated profile and the
Stage 5 verification results. Same input -> same output, every time.

    DEMO/RESEARCH RUBRIC. The criteria, weights and thresholds below are
    illustrative choices for a student project. They are NOT LNMIIT's
    hiring criteria and carry no institutional authority.

THREE OUTPUTS, KEPT SEPARATE ON PURPOSE
  1. eligibility  - a gate: met / not_met / indeterminate. Never a number,
                    never affected by the score, never affecting it.
  2. score        - per-criterion points, each traceable to a source field.
  3. flags        - observations for a human. They move nothing.

Collapsing any of these into the others would destroy information the
reviewer needs. See DESIGN_DECISIONS.md sections 3 and 4.

EVERY NUMBER CARRIES ITS PROVENANCE
  Each criterion reports the source field(s) it read and the evidence
  behind them. A criterion with no evidence scores 0 and says
  "insufficient evidence" - which must stay distinguishable from scoring 0
  on present-but-weak evidence, because those call for different decisions.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402
import roles   # noqa: E402  the shared role taxonomy
import degrees        # noqa: E402  degree level, equivalence, branch
import institutions   # noqa: E402  institution tiers (config-driven)

RUBRIC_VERSION = "demo-rubric-v2"

# --- Weights. They sum to 100 (asserted at import). ------------------------
#
# REVISED 2026-10-29 on faculty instruction. The three top-level
# categories and their totals were set by the department; the split
# WITHIN each category is recorded here with its reasoning so that a
# later reviewer can see which numbers were mandated and which were
# proposed.
#
#   Research and related contributions .... 40   (mandated)
#   Experience ............................ 25   (mandated)
#   Education ............................. 35   (mandated)
#
# WHAT CHANGED INSIDE RESEARCH, and why. The previous rubric spent 45
# marks on research (publications 40 + visibility 5), which the revised
# total of 40 cannot accommodate. Publications absorbed the whole
# reduction, 40 -> 35, and visibility was left at 5. Rescaling the larger
# component is the change that disturbs least: every reviewed rule
# underneath it - the credit matrix, the exclusions, the patent ratios and
# the 25% patent ceiling - keeps working untouched, and only the
# multiplier moves. Moving marks onto citations instead would have
# quietly re-weighted the rubric towards established researchers and
# against strong early-career candidates, which nobody asked for.
CATEGORIES = {
    "research": ("publications", "research_visibility"),
    "experience": ("teaching_experience", "industry_experience"),
    "education": ("doctorate", "postdoctoral", "masters", "bachelors"),
}

WEIGHTS = {
    # --- Research and related contributions: 40 ---------------------------
    "publications": 35,          # papers + patents, patents capped at 25%
    "research_visibility": 5,    # citations of corroborated work
    # --- Experience: 25 ---------------------------------------------------
    "teaching_experience": 20,
    "industry_experience": 5,
    # --- Education: 35 ----------------------------------------------------
    "doctorate": 15,
    "postdoctoral": 5,
    "masters": 8,
    "bachelors": 7,
}
assert sum(WEIGHTS.values()) == 100, "weights must sum to 100"

CATEGORY_TOTALS = {name: sum(WEIGHTS[k] for k in keys)
                   for name, keys in CATEGORIES.items()}
assert CATEGORY_TOTALS == {"research": 40, "experience": 25, "education": 35}, \
    "category totals must match the approved rubric"
assert set(WEIGHTS) == {k for keys in CATEGORIES.values() for k in keys}, \
    "every weight must belong to exactly one category"

# --- Saturation points: the level at which a criterion earns full marks. ---
# Set deliberately low for a DEMO rubric; they are not sector norms.
# Raised from 10 to 20 (user decision, 2026-10-15). At 10, Candidate_04's
# 19 corroborated papers scored identically to a hypothetical 10-paper
# candidate: the criterion stopped discriminating exactly among the
# strongest applicants, where it matters most. Like every number in this
# rubric, it is a DEMO/RESEARCH choice, not a validated sector norm.
FULL_MARKS_PUBLICATION_CREDIT = 20.0   # credited publications (see schema)
FULL_MARKS_TEACHING_YEARS = 5.0
FULL_MARKS_INDUSTRY_YEARS = 3.0
FULL_MARKS_CITATIONS = 100

# Postdoctoral experience, measured the same way as every other duration:
# overlapping appointments merged, ongoing posts resolved against as_of.
#
# WHY TWO YEARS. A postdoc is normally a fixed two-to-three year term, so
# two years represents one completed appointment. Saturating there means
# the criterion rewards having done a postdoc rather than rewarding a long
# one - a candidate on their fourth consecutive short contract is not
# twice the scholar of one who finished a single term and moved on.
# DEMO/RESEARCH value; needs faculty approval.
FULL_MARKS_POSTDOC_YEARS = 2.0

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


# ---------------------------------------------------------------------------
# Dates and durations
# ---------------------------------------------------------------------------

def parse_date(value) -> tuple[int, int] | None:
    """
    Parse a CV date to (year, month). None when genuinely unreadable.

    Day precision is accepted and discarded - durations here are measured
    in whole months, so the day adds nothing but a parsing failure.

    FORMATS, and why each is here. Candidate_06's CV printed
    "Oct 4, 2024" for an Assistant Professor post. Only month-precision
    forms were handled, so the date failed to parse, the position was
    discarded as unusable, and the criterion scored 0 with "no teaching
    position with a readable date range" - a real, documented, currently
    held academic appointment reported as absent evidence. A parser that
    rejects an ordinary way of writing a date is not a safeguard, it is a
    bug, and it fails in the direction this project most wants to avoid.
    """
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    text = text.replace(",", " ")
    text = re.sub(r"\s+", " ", text).strip()

    def month_of(token: str):
        return MONTHS.get(token[:3])

    # 2025-08  /  2025-08-14  /  2025/08
    iso = re.match(r"^(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?$", text)
    if iso:
        month = int(iso.group(2))
        if 1 <= month <= 12:
            return int(iso.group(1)), month

    # 2025
    if re.match(r"^\d{4}$", text):
        return int(text), 1

    # aug 2025  /  august 2025  /  aug. 2025  /  sept 2024
    named = re.match(r"^([a-z]{3,9})\.? (\d{4})$", text)
    if named and month_of(named.group(1)):
        return int(named.group(2)), month_of(named.group(1))

    # oct 4 2024  /  october 4 2024   (day dropped)
    named_day = re.match(r"^([a-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)? (\d{4})$", text)
    if named_day and month_of(named_day.group(1)):
        return int(named_day.group(3)), month_of(named_day.group(1))

    # 4 oct 2024  /  4th october 2024
    day_named = re.match(r"^(\d{1,2})(?:st|nd|rd|th)? ([a-z]{3,9})\.? (\d{4})$", text)
    if day_named and month_of(day_named.group(2)):
        return int(day_named.group(3)), month_of(day_named.group(2))

    # 2024 oct
    rev = re.match(r"^(\d{4}) ([a-z]{3,9})\.?$", text)
    if rev and month_of(rev.group(2)):
        return int(rev.group(1)), month_of(rev.group(2))

    # 04/10/2024 is ambiguous (day-first vs month-first) and is NOT
    # guessed at: an unreadable date is reported, never invented.
    return None


def _months(point: tuple[int, int]) -> int:
    return point[0] * 12 + (point[1] - 1)


# --- Teaching role kind, and its weighting --------------------------------
# A teaching assistantship or lab demonstration is real teaching, but it is
# less load and less autonomy than running a course as faculty. Counting
# them per-month identically overstates the assistantship; counting them at
# zero would erase the only teaching most early-career candidates have.
#
# 0.5 is a judgement about a KNOWN role - the CV says "Teaching Assistant" -
# not an estimate of an unknown, which is what makes it different from the
# in-progress doctorate case (DESIGN_DECISIONS.md sections 9 and 10).
ASSISTANTSHIP_CREDIT = 0.5


def classify_teaching_role(position: dict) -> str:
    """
    'assistantship' | 'appointment' | 'uncertain'.

    The vocabulary and the head-noun rule live in roles.py, which both this
    module and Stage 4 consult, so a title cannot be read one way when it
    is validated and another way when it is scored.

    'uncertain' is a real answer. Where the CV's wording and structure do
    not settle it, the pipeline says so rather than quietly picking a side
    (user decision, 2026-10-15).
    """
    return roles.teaching_kind(position)[0]


def classify_teaching_role_reason(position: dict) -> str:
    """The justification behind classify_teaching_role, for the audit log."""
    return roles.teaching_kind(position)[1]


def subtract_intervals(base: list[tuple[int, int]],
                       remove: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Months in `base` not covered by `remove`."""
    result = []
    for start, end in base:
        pieces = [(start, end)]
        for cut_start, cut_end in remove:
            nxt = []
            for piece_start, piece_end in pieces:
                if cut_end <= piece_start or cut_start >= piece_end:
                    nxt.append((piece_start, piece_end))
                    continue
                if cut_start > piece_start:
                    nxt.append((piece_start, cut_start))
                if cut_end < piece_end:
                    nxt.append((cut_end, piece_end))
            pieces = nxt
        result.extend(pieces)
    return result


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping [start, end) month ranges."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:                 # overlapping or touching
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def duration_years(positions: list[dict], categories: set[str],
                   as_of: date) -> tuple[float, list[dict], list[str]]:
    """
    Total years across `categories`, with OVERLAPPING ROLES MERGED.

    Merging rather than summing is deliberate: a candidate holding a
    lectureship and a teaching assistantship in the same months has not
    lived through twice the time. Summing would let concurrent roles
    inflate a total past wall-clock duration.

    Returns (years, evidence rows, problems).
    """
    intervals, evidence, problems = [], [], []

    for position in positions:
        if (position.get("category") or "").lower() not in categories:
            continue

        start = parse_date(position.get("start_date"))
        if start is None:
            problems.append(
                f"{position.get('title') or 'untitled role'}: unreadable or "
                f"missing start date {position.get('start_date')!r}")
            continue

        if position.get("is_current") or not position.get("end_date"):
            # "Present" resolves against a RECORDED run date, so the same CV
            # does not silently score differently next year.
            end = (as_of.year, as_of.month)
        else:
            end = parse_date(position.get("end_date"))
            if end is None:
                problems.append(
                    f"{position.get('title') or 'untitled role'}: unreadable "
                    f"end date {position.get('end_date')!r}")
                continue

        start_m, end_m = _months(start), _months(end)
        if end_m < start_m:
            problems.append(
                f"{position.get('title') or 'untitled role'}: end precedes start")
            continue

        intervals.append((start_m, end_m))
        evidence.append({
            "title": position.get("title"),
            "organisation": position.get("organisation"),
            "category": position.get("category"),
            "from": position.get("start_date"),
            "to": position.get("end_date") or f"present ({as_of.isoformat()})",
            "months": end_m - start_m,
            "evidence": position.get("evidence", ""),
        })

    total_months = sum(end - start for start, end in merge_intervals(intervals))
    return round(total_months / 12.0, 2), evidence, problems


# ---------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------

@dataclass
class Criterion:
    name: str
    weight: int
    raw: float                 # the measured quantity, in its own units
    unit: str
    fraction: float            # 0.0 - 1.0 of the weight earned
    points: float
    sources: list[str] = field(default_factory=list)
    evidence: list = field(default_factory=list)
    insufficient_evidence: bool = False
    note: str = ""
    unclassified_roles: list = field(default_factory=list)
    # Roles the CV documents that this criterion could NOT count, with the
    # reason. A position whose category cannot be determined contributes
    # nothing - but it must never do so invisibly, or a gap in our reading
    # of the CV is presented to the reviewer as an absence of experience.
    unscored_positions: list = field(default_factory=list)
    # Research-output breakdown, so the two kinds of contribution stay
    # legible instead of collapsing into one number.
    publication_credit: float = 0.0
    patent_credit: float = 0.0
    patent_credit_counted: float = 0.0
    # True when the pipeline could not reach a verdict - distinct from
    # scoring zero on evidence. See DESIGN_DECISIONS.md section 12.
    indeterminate: bool = False
    indeterminate_entries: int = 0
    # Which of the three approved categories this criterion rolls up into.
    category: str = ""
    # The multipliers behind an education score, kept as separate named
    # factors rather than folded into one opaque number. A reviewer who
    # disagrees with the score needs to see WHICH factor they disagree
    # with, and each of these is independently overridable by a human.
    factors: list = field(default_factory=list)
    # A criterion whose score rests on something a human should confirm.
    needs_review: bool = False
    review_reasons: list = field(default_factory=list)
    # The level at which this criterion earns full marks, in its own
    # units. Carried on the criterion so an explanation can say "5.25 of
    # the 20 needed" without re-deriving a scoring rule of its own.
    full_marks_at: float | None = None

    def as_dict(self) -> dict:
        return {
            "criterion": self.name,
            "full_marks_at": self.full_marks_at,
            "category": self.category,
            "factors": self.factors,
            "needs_review": self.needs_review,
            "review_reasons": self.review_reasons,
            "weight": self.weight,
            "measured": self.raw,
            "unit": self.unit,
            "fraction_of_weight": round(self.fraction, 4),
            "points": round(self.points, 2),
            "source_fields": self.sources,
            "evidence": self.evidence,
            "insufficient_evidence": self.insufficient_evidence,
            "note": self.note,
            "unclassified_roles": self.unclassified_roles,
            "unscored_positions": self.unscored_positions,
            "publication_credit": self.publication_credit,
            "patent_credit": self.patent_credit,
            "patent_credit_counted": self.patent_credit_counted,
            "indeterminate": self.indeterminate,
            "indeterminate_entries": self.indeterminate_entries,
        }


def _capped(value: float, full_marks_at: float) -> float:
    if full_marks_at <= 0:
        return 0.0
    return max(0.0, min(1.0, value / full_marks_at))


def patent_credit(patents: list[dict]) -> tuple[float, list[dict]]:
    """
    Patent credit in PUBLICATION-EQUIVALENTS, with exclusions applied.

    Shares the publication scale deliberately, so one saturation point
    governs the whole research criterion and a reviewer can read "this
    candidate's output is worth N papers" across both kinds of work.
    """
    total, rows = 0.0, []
    for patent in patents:
        exclusions = patent.get("credit_exclusions") or []
        status = patent.get("status_final", "unknown")

        if exclusions:
            credit = 0.0
            reason = f"excluded: {', '.join(exclusions)}"
        else:
            credit = schema.PATENT_CREDIT.get(status, 0.0)
            reason = (f"{status} patent, credited at {credit} of a "
                      f"corroborated publication")
            if credit == 0.0:
                reason = (f"{status}: no examination has taken place, so "
                          f"this earns no credit yet")

        total += credit
        rows.append({
            "title": patent.get("title"),
            "number": patent.get("number"),
            "status": status,
            "credit": credit,
            "reason": reason,
            "inventors": patent.get("inventors") or [],
            "verification": patent.get("verification", "unavailable"),
            "verification_note": patent.get("verification_note", ""),
            "duplicate_of": patent.get("duplicate_of"),
        })
    return total, rows


def score_publications(verified: list[dict],
                       patents: list[dict] | None = None) -> Criterion:
    """
    Research output: peer-reviewed publications PLUS patents.

    Publication credit comes from schema.CREDIT_MATRIX with
    schema.CREDIT_EXCLUSIONS applied first. Patent credit comes from
    schema.PATENT_CREDIT, on the same publication-equivalent scale, and is
    CAPPED at schema.PATENT_SHARE_OF_RESEARCH of the criterion so it can
    supplement peer-reviewed work without substituting for it.

    The two are summed into one criterion rather than scored separately,
    so that disciplines which do not patent are not handed an automatic
    zero on a scored line (see schema.py section 4b).
    """
    weight = WEIGHTS["publications"]
    total, rows, indeterminate = 0.0, [], 0

    for entry in verified:
        # Normalised HERE as well as in Stage 4. Validation already does
        # it, but this is the line that turns a status into credit, and it
        # must not depend on every caller having validated first: an
        # unrecognised word read straight as "unknown" inherits the
        # full-credit promotion path. See schema.normalise_status.
        status, _ = schema.normalise_status(entry.get("status"))
        status_class = schema.STATUS_CLASS.get(status, "unknown")
        outcome = entry["verification"]["outcome"]
        exclusions = entry.get("credit_exclusions") or []

        if exclusions:
            # Plain words for the professor, from the same table that
            # defines the rule, so wording and policy cannot drift apart.
            # The raw ids stay in "exclusions" for the audit.
            credit, reason = 0.0, "not counted: " + "; ".join(
                schema.EXCLUSION_LABEL.get(e, e.replace("_", " "))
                for e in exclusions)
        else:
            matrix = schema.CREDIT_MATRIX[(status_class, outcome)]
            if matrix is schema.INDETERMINATE:
                credit, reason = 0.0, "lookup failed - INDETERMINATE, needs review"
                indeterminate += 1
            else:
                credit, reason = matrix, f"{status_class} + {outcome}"

        # Visibility rule (DESIGN_DECISIONS.md section 2): an unlabelled,
        # uncorroborated claim earns nothing but must stay in front of the
        # reviewer, with wording about OUR knowledge, not their honesty.
        note = ""
        if not exclusions and status_class == "unknown" and outcome in (
                "not_found", "weak_match", "unconfirmed_author"):
            note = "Unlabeled claim, not independently verifiable."

        # Credited, but Stage 4 could not place the quoted citation line.
        # The entry stays scored - its title is in the CV - and the doubt
        # is shown rather than resolved by deleting the entry.
        if entry.get("validation_verdict") == "needs_review":
            note = (note + " " if note else "") + (
                "The quoted citation line could not be matched to the CV; "
                "the title was. Please check this entry.")

        total += credit
        rows.append({
            "title": entry.get("title"),
            "status": status,
            "status_class": status_class,
            "excluded": bool(exclusions),
            "exclusions": list(exclusions),
            "duplicate_of": entry.get("duplicate_of"),
            "verification": outcome,
            "credit": credit,
            "reason": reason,
            "note": note,
            "openalex": entry["verification"].get("openalex_id"),
            "query_url": entry["verification"].get("query_url"),
        })

    # --- patents, on the same scale, then capped -------------------------
    patent_total, patent_rows = patent_credit(patents or [])
    patent_ceiling = FULL_MARKS_PUBLICATION_CREDIT * schema.PATENT_SHARE_OF_RESEARCH
    patent_counted = min(patent_total, patent_ceiling)
    patent_capped = patent_total > patent_ceiling

    combined = total + patent_counted
    fraction = _capped(combined, FULL_MARKS_PUBLICATION_CREDIT)

    criterion = Criterion(
        name="publications",
        weight=weight,
        raw=round(combined, 2),
        unit="publication-equivalents (papers + capped patent credit)",
        fraction=fraction,
        points=weight * fraction,
        sources=["publications[].status", "verification.outcome",
                 "patents[].status"],
        evidence=rows + [dict(row, kind="patent") for row in patent_rows],
        insufficient_evidence=(not verified and not patent_rows),
        note=(f"full marks at {FULL_MARKS_PUBLICATION_CREDIT} "
              f"publication-equivalents"),
    )
    criterion.full_marks_at = FULL_MARKS_PUBLICATION_CREDIT
    criterion.patent_credit = round(patent_total, 2)
    criterion.patent_credit_counted = round(patent_counted, 2)
    criterion.publication_credit = round(total, 2)

    if patent_rows:
        criterion.note += (
            f"; of which {round(patent_counted, 2)} from "
            f"{len(patent_rows)} patent(s)")
        if patent_capped:
            criterion.note += (
                f" (capped: patents may contribute at most "
                f"{patent_ceiling} publication-equivalents, "
                f"{round(patent_total, 2)} claimed)")
        criterion.note += (
            "; patent records were NOT checked against any patent register")

    if not verified and not patent_rows:
        criterion.note = ("no publications or patents listed - "
                          "insufficient evidence")
    elif indeterminate:
        # The matrix says INDETERMINATE for these; the arithmetic has to
        # put SOME number in, and 0.0 is the only safe one. But a 0 that
        # means "we could not check" must never be presented as a 0 that
        # means "nothing to credit" - that is a system failure dressed up
        # as a finding about a person. Flag it loudly instead.
        criterion.indeterminate = True
        criterion.indeterminate_entries = indeterminate
        criterion.note += (
            f"; {indeterminate} of {len(verified)} entr(ies) could NOT BE "
            f"LOOKED UP (service unreachable). This score is not reliable "
            f"and must not be read as a judgement on the candidate's "
            f"publication record - re-run when the index is reachable")
    return criterion


def teaching_years_weighted(positions: list[dict],
                            as_of: date) -> tuple[float, list[dict], list[str], list[str]]:
    """
    Teaching years with assistantship months counted at ASSISTANTSHIP_CREDIT.

    Appointment and assistantship intervals are merged SEPARATELY, then any
    months covered by both are removed from the assistantship side. A month
    in which someone was simultaneously a professor and a TA is counted once,
    as the professorship - the higher-responsibility role wins rather than
    the two stacking past wall-clock time.

    RANK (added 2026-10-29). Appointments are bucketed by seniority and
    each bucket is merged on its own, then every bucket has the months
    already claimed by a MORE senior one subtracted from it. That keeps
    the original guarantee intact: a month can be counted once and only
    once, at the best rate it qualifies for. Simply multiplying each
    role's months by its rank would have let a concurrent Assistant and
    Associate professorship bill 2.15 months of credit for one month of
    work, which is the exact inflation the merge exists to prevent.

    Returns (effective years, evidence rows, date problems, uncertainty notes).
    """
    # Ordered most senior first - the subtraction below depends on it.
    rank_order = ["professor", "associate_professor", "assistant_professor",
                  "faculty"]
    buckets: dict[str, list[tuple[int, int]]] = {
        **{rank: [] for rank in rank_order}, "assistantship": []}
    evidence, problems, uncertain = [], [], []

    for position in positions:
        if (position.get("category") or "").lower() != "teaching":
            continue

        start = parse_date(position.get("start_date"))
        if start is None:
            problems.append(f"{position.get('title') or 'untitled role'}: "
                            f"unreadable start date "
                            f"{position.get('start_date')!r}")
            continue
        if position.get("is_current") or not position.get("end_date"):
            end = (as_of.year, as_of.month)
        else:
            end = parse_date(position.get("end_date"))
            if end is None:
                problems.append(f"{position.get('title') or 'untitled role'}: "
                                f"unreadable end date "
                                f"{position.get('end_date')!r}")
                continue

        start_m, end_m = _months(start), _months(end)
        if end_m < start_m:
            problems.append(f"{position.get('title') or 'untitled role'}: "
                            f"end precedes start")
            continue

        kind, why = roles.teaching_kind(position)
        if kind == "uncertain":
            # Counted at the FULL rate, and said out loud. Defaulting
            # downward would penalise the candidate for our inability to
            # read their CV; defaulting silently either way is what the
            # reviewer asked us not to do.
            uncertain.append(
                f"'{position.get('title')}' could not be classified as a "
                f"teaching assistantship or a faculty appointment from the "
                f"CV - counted at the full rate; please check.")
            kind = "appointment"

        if kind == "appointment":
            rank, rate, rank_why = roles.faculty_rank(position)
            bucket = rank
        else:
            rank, rate, rank_why = (
                "assistantship", ASSISTANTSHIP_CREDIT,
                "a teaching assistantship, counted at half rate because it "
                "carries less load and less autonomy than a faculty post")
            bucket = "assistantship"

        buckets[bucket].append((start_m, end_m))
        evidence.append({
            "title": position.get("title"),
            "organisation": position.get("organisation"),
            "kind": kind,
            "kind_reason": why,
            "rank": rank,
            "rank_reason": rank_why,
            "credit_per_month": rate,
            "from": position.get("start_date"),
            "to": position.get("end_date") or f"present ({as_of.isoformat()})",
            "months": end_m - start_m,
            "evidence": position.get("evidence", ""),
        })

    # Each tier keeps only the months no more senior tier already claimed,
    # so overlapping appointments can never bill the same month twice.
    effective = 0.0
    claimed: list[tuple[int, int]] = []
    for rank in rank_order:
        own = subtract_intervals(merge_intervals(buckets[rank]), claimed)
        effective += roles.RANK_CREDIT[rank] * sum(e - s for s, e in own)
        claimed = merge_intervals(claimed + own)

    assistantship = subtract_intervals(
        merge_intervals(buckets["assistantship"]), claimed)
    effective += ASSISTANTSHIP_CREDIT * sum(e - s for s, e in assistantship)

    return round(effective / 12.0, 2), evidence, problems, uncertain


def uncategorised_positions(positions: list[dict]) -> list[dict]:
    """
    Documented roles that no experience criterion can count.

    These are positions the CV plainly states but whose nature could not be
    determined - typically an employer known only by a proper noun, with a
    title that names no academic role. The honest answer is that we cannot
    tell, and the reviewer has to be TOLD that, because the alternative is
    a zero that reads as "no such experience" when it means "we could not
    read this".
    """
    unscored = []
    for position in positions:
        category = (position.get("category") or "other").lower()
        review = position.get("category_review") or {}
        if category in ("teaching", "research", "industry"):
            continue
        if not review.get("needs_review"):
            continue          # legitimately 'other', e.g. training
        unscored.append({
            "title": position.get("title"),
            "organisation": position.get("organisation"),
            "from": position.get("start_date"),
            "to": position.get("end_date") or (
                "present" if position.get("is_current") else None),
            "reason": review.get("reason", "category could not be determined"),
            "evidence": position.get("evidence", ""),
        })
    return unscored


def score_experience(positions: list[dict], categories: set[str],
                     key: str, full_marks_at: float, as_of: date) -> Criterion:
    weight = WEIGHTS[key]
    uncertain: list[str] = []

    if categories == {"teaching"}:
        years, evidence, problems, uncertain = teaching_years_weighted(
            positions, as_of)
    else:
        years, evidence, problems = duration_years(positions, categories, as_of)

    fraction = _capped(years, full_marks_at)

    criterion = Criterion(
        name=key,
        weight=weight,
        raw=years,
        unit=("years (overlapping merged; assistantships at "
              f"{ASSISTANTSHIP_CREDIT})" if categories == {"teaching"}
              else "years (overlapping roles merged)"),
        fraction=fraction,
        points=weight * fraction,
        sources=["positions[].category", "positions[].start_date",
                 "positions[].end_date"],
        evidence=evidence,
        insufficient_evidence=(not evidence),
        note=f"full marks at {full_marks_at} years",
    )
    if not evidence:
        criterion.note = (f"no {'/'.join(sorted(categories))} position with a "
                          f"readable date range - insufficient evidence")
    if problems:
        criterion.note += "; unusable entries: " + "; ".join(problems)
    if uncertain:
        criterion.note += "; UNCLASSIFIED: " + " ".join(uncertain)
        criterion.unclassified_roles = uncertain

    # Roles we could not categorise are reported on EVERY experience
    # criterion, because any one of them might have been the criterion
    # that should have counted it.
    unscored = uncategorised_positions(positions)
    if unscored:
        criterion.unscored_positions = unscored
        criterion.note += (
            f"; {len(unscored)} documented role(s) could not be categorised "
            f"from the CV and are counted toward NO criterion - this is a "
            f"gap in our reading, not an absence of experience; please "
            f"classify: "
            + "; ".join(f"{u['title']!r}"
                        + (f" at {u['organisation']!r}" if u["organisation"]
                           else "")
                        for u in unscored))
    return criterion


def institution_review_reason(tier: dict, institution_name: str | None) -> str:
    """
    What to tell the reviewer when an institution needs confirming.

    There are TWO different uncertainties and they must not be worded the
    same way, because the wrong one is a false statement about the score:

      unknown / absent  the configuration has no entry for this name, so
                        the qualification earned the BASE rate.
      a flagged match   the family is recognised - an IIT with no campus
                        named, say - and the approved rule gave it a real
                        tier. Telling the professor it "is not in the
                        approved list and was scored at the base rate"
                        would be wrong on both counts.

    Shared by the doctorate and the bachelor's/master's paths so one
    hierarchy cannot be explained two ways (requirement 5).
    """
    named = institution_name or "not named on the CV"
    reason = (tier.get("reason") or "").rstrip(". ")
    # .capitalize() would lowercase the remainder and flatten IIT to iit.
    reason = reason[:1].upper() + reason[1:] if reason else ""

    if tier["tier"] in ("unknown", "absent"):
        return (f"The awarding institution ({named}) is not in the approved "
                f"list, so it was scored at the base rate. {reason}.")
    return (f"The awarding institution ({named}) was counted as "
            f"{tier['label']}, at {tier['multiplier']} of the institution "
            f"marks, but the match is not certain. {reason}.")


def score_doctorate(profile: dict, gate: dict) -> Criterion:
    """
    Scored SEPARATELY from the eligibility gate, and never reads it.

    The gate decides whether the person may be appointed at all; this
    criterion reflects the qualification as one input among several. They
    are computed independently so neither can quietly contaminate the
    other (DESIGN_DECISIONS.md section 3).
    """
    weight = WEIGHTS["doctorate"]
    status = _value(profile, "phd_status")
    year = _value(profile, "phd_year")

    # A withheld claim is NOT the same as absent evidence, and the output
    # must keep them apart: one means "we could not confirm this", the
    # other means "there is nothing here". Only the second is
    # insufficient_evidence.
    withheld = _rejected(profile, "phd_status")

    if status == "awarded" and year:
        fraction, note, thin = 1.0, f"doctorate awarded {year}", False
    elif status == "thesis_submitted" or _value(
            profile, "highest_degree") == "phd_submitted":
        # Part marks, and flagged. The scholarly work is complete and
        # under examination; the award is not conferred. See
        # schema.DOCTORATE_STAGE_CREDIT for why this is not zero and not
        # one, and note that the eligibility gate stays 'indeterminate'.
        fraction = schema.DOCTORATE_STAGE_CREDIT["thesis_submitted"]
        thin = False
        note = (f"doctoral thesis submitted and under examination, award "
                f"not yet conferred - counted at "
                f"{int(fraction * 100)}% of the doctorate marks")
    elif status == "pursuing" or _value(profile, "highest_degree") == "phd_pursuing":
        # ZERO, not a part-mark (user decision, 2026-10-15). Awarding 0.5
        # for an unfinished doctorate would be guessing how far along it
        # is - a first-year and a thesis-submitted candidate are not the
        # same, and nothing in a CV reliably tells them apart. Whether an
        # in-progress PhD makes someone considerable at all is already
        # answered, separately, by the eligibility gate.
        fraction, note, thin = 0.0, ("doctorate in progress, not yet awarded - "
                                     "no credit until the award is evidenced"), False
    elif withheld:
        fraction, thin = 0.0, False
        note = ("a doctorate was reported but Stage 4 withheld the claim - "
                "scored 0 pending human review, NOT a finding that none exists")
    else:
        fraction, note, thin = 0.0, "no doctorate evidenced - insufficient evidence", True

    # The awarding institution scales a doctorate that EXISTS. It can
    # never create one: a tier-1 name cannot lift an unfinished PhD above
    # zero, which is why the multiplier is applied to `fraction` after the
    # award test rather than inside it.
    factors = [{"factor": "qualification", "value": fraction,
                "reason": note}]
    review_reasons = []
    institution_name = _value(profile, "phd_institution")

    if status == "thesis_submitted" or _value(
            profile, "highest_degree") == "phd_submitted":
        review_reasons.append(
            "The CV states the doctoral thesis has been submitted but does "
            "not show the degree as awarded. It has been given part of the "
            "doctorate marks, not all of them, and the candidate is NOT "
            "treated as holding a PhD for eligibility. Please confirm the "
            "stage, and override once the award is evidenced.")

    if fraction > 0:
        tier = institutions.classify(institution_name)
        fraction *= tier["multiplier"]
        factors.append({
            "factor": "institution",
            "value": tier["multiplier"],
            "tier": tier["tier"],
            "label": tier["label"],
            "institution": institution_name,
            "reason": tier["reason"],
        })
        note += (f"; awarded by {institution_name or 'an unnamed institution'}"
                 f" ({tier['tier'].replace('_', ' ')}, "
                 f"x{tier['multiplier']})")
        if tier["needs_review"]:
            review_reasons.append(
                institution_review_reason(tier, institution_name))

    return Criterion(
        name="doctorate",
        weight=weight,
        raw=round(fraction, 4),
        unit="fraction of the doctorate marks earned",
        fraction=fraction,
        points=weight * fraction,
        sources=["phd_status", "phd_year", "highest_degree",
                 "phd_institution"],
        evidence=[{
            "qualification": ("a doctorate (thesis submitted, not yet "
                              "awarded)"
                              if status == "thesis_submitted"
                              else "a doctorate (in progress)"
                              if status == "pursuing"
                              else "a doctorate (PhD)"),
            "organisation": institution_name,
            "status": status or "not stated",
            "year": year,
            "reason": note,
        }],
        insufficient_evidence=thin,
        note=note,
        factors=factors,
        needs_review=bool(review_reasons),
        review_reasons=review_reasons,
    )


def score_postdoctoral(positions: list[dict], profile: dict,
                       as_of: date) -> Criterion:
    """
    Postdoctoral research experience - its own category, 5 marks.

    Scored on verified duration, with overlapping appointments merged so
    two concurrent fellowships cannot bill the same months twice.

    WHAT IS DELIBERATELY NOT COUNTED. Only positions roles.is_postdoc()
    recognises from explicit wording. A doctorate itself ("Research
    Scholar", "JRF/SRF") is the stage BEFORE this one, and ordinary
    research employment ("Research Associate", "Research Scientist") is
    held before, during and after a doctorate. Crediting either would
    hand this category to candidates who never held a postdoc.

    A post that genuinely is a postdoc but is printed under an unusual
    title scores nothing HERE - it is still counted as research
    experience, and the reviewer is told we could not identify it, so it
    never becomes a silent zero.
    """
    weight = WEIGHTS["postdoctoral"]
    intervals, evidence, problems = [], [], []
    review_reasons = []

    for position in positions:
        postdoc, why = roles.is_postdoc(position)
        if not postdoc:
            continue

        start = parse_date(position.get("start_date"))
        if start is None:
            problems.append(
                f"{position.get('title') or 'untitled role'}: unreadable or "
                f"missing start date {position.get('start_date')!r}")
            continue
        if position.get("is_current") or not position.get("end_date"):
            end = (as_of.year, as_of.month)
        else:
            end = parse_date(position.get("end_date"))
            if end is None:
                problems.append(
                    f"{position.get('title') or 'untitled role'}: unreadable "
                    f"end date {position.get('end_date')!r}")
                continue

        start_m, end_m = _months(start), _months(end)
        if end_m < start_m:
            problems.append(f"{position.get('title') or 'untitled role'}: "
                            f"end precedes start")
            continue

        intervals.append((start_m, end_m))
        evidence.append({
            "title": position.get("title"),
            "organisation": position.get("organisation"),
            "kind": "postdoctoral",
            "kind_reason": why,
            "from": position.get("start_date"),
            "to": position.get("end_date") or f"present ({as_of.isoformat()})",
            "months": end_m - start_m,
            "evidence": position.get("evidence", ""),
        })

    months = sum(end - start for start, end in merge_intervals(intervals))
    years = round(months / 12.0, 2)
    fraction = _capped(years, FULL_MARKS_POSTDOC_YEARS)

    note = f"full marks at {FULL_MARKS_POSTDOC_YEARS} years"
    if not evidence:
        note = ("no postdoctoral appointment stated in the CV - "
                "insufficient evidence")
    if problems:
        note += "; unusable entries: " + "; ".join(problems)

    # A postdoc without a completed doctorate is a contradiction, not a
    # score. Flagged rather than silently discounted: the usual cause is a
    # CV that states the postdoc and omits the PhD award year, and
    # punishing that would be punishing our own reading.
    if evidence and _value(profile, "phd_status") != "awarded":
        review_reasons.append(
            "A postdoctoral appointment is listed but the CV does not "
            "confirm a completed doctorate. Both have been scored as "
            "stated; please confirm the sequence.")

    return Criterion(
        name="postdoctoral",
        weight=weight,
        raw=years,
        unit="years (overlapping appointments merged)",
        fraction=fraction,
        points=weight * fraction,
        sources=["positions[].title", "positions[].start_date",
                 "positions[].end_date"],
        evidence=evidence,
        insufficient_evidence=(not evidence),
        note=note,
        factors=[{"factor": "duration", "value": years,
                  "reason": f"{years} years of documented postdoctoral work"}],
        needs_review=bool(review_reasons),
        review_reasons=review_reasons,
    )


def _score_degree(profile: dict, level: str, research_context: str,
                  department: str) -> Criterion:
    """
    One education category below the doctorate: bachelor's or master's.

    FOUR INDEPENDENT FACTORS, multiplied, each reported by name:

        points = weight x equivalence x institution x branch

      equivalence  how close the qualification is to the one advertised
                   at this level (B.Tech 1.00, BCA 0.85, B.Sc 0.75 ...)
      institution  the awarding body's tier
      branch       whether the SUBJECT fits the department

    They are kept as separate named factors rather than collapsed into
    one number because a reviewer who disagrees with the score needs to
    see which factor they disagree with - and each is independently
    overridable.

    INTEGRATED AND DUAL DEGREES confer both levels, so a single
    "Integrated M.Tech" row is scored in BOTH this category and the one
    below it. It cannot double-count within a category: each category
    takes its single best qualification and is capped at its own weight.
    """
    weight = WEIGHTS[level]
    institution_name = _value(profile, f"{level}_institution")
    year = _value(profile, f"{level}_year")
    review_reasons = []

    # The extractor intermittently returns the controlled-vocabulary word
    # ("masters") where the qualification name belongs. Reading the
    # verbatim CV span it quoted recovers the real name without inventing
    # one; when the span does not settle it either, the name stays None
    # and the criterion is flagged rather than guessed.
    name, recovered = degrees.name_from_evidence(
        _value(profile, f"{level}_degree"),
        _evidence(profile, f"{level}_degree"), level)
    if recovered:
        review_reasons.append(
            f"The qualification name was recovered from the CV text because "
            f"{recovered}. Please confirm it.")

    subject = _value(profile, f"{level}_subject") or name

    # An integrated degree one level up also confers this level.
    conferred_by = None
    if not name:
        higher = "masters" if level == "bachelors" else None
        higher_name = _value(profile, f"{higher}_degree") if higher else None
        if higher_name and degrees.is_integrated(higher_name):
            name, conferred_by = higher_name, higher
            institution_name = (institution_name
                                or _value(profile, f"{higher}_institution"))
            subject = subject or _value(profile, f"{higher}_subject")

    if not name:
        return Criterion(
            name=level, weight=weight, raw=0.0,
            unit="fraction of the category earned", fraction=0.0, points=0.0,
            sources=[f"{level}_degree", f"{level}_institution"],
            evidence=[], insufficient_evidence=True,
            note=(f"no {level[:-1] if level.endswith('s') else level} degree "
                  f"is stated in the CV - insufficient evidence, which is "
                  f"not the same as a finding that the candidate holds none"),
            needs_review=True,
            review_reasons=[
                f"The CV states no {'bachelor' if level == 'bachelors' else 'master'}"
                f"'s degree. Scored 0 because there is nothing to score, not "
                f"because the qualification was judged absent. Please check "
                f"the CV."],
        )

    ratio, equivalence_label, equivalence_unclear = degrees.equivalence(
        name, level)
    tier = institutions.classify(institution_name)
    branch = degrees.branch_relevance(subject, department, research_context)

    fraction = ratio * tier["multiplier"] * branch["ratio"]

    if equivalence_unclear:
        review_reasons.append(
            f"The qualification {name!r} does not match any entry in the "
            f"approved equivalence table, so it was scored at the generic "
            f"rate of {ratio}. Please confirm.")
    if tier["needs_review"]:
        review_reasons.append(
            institution_review_reason(tier, institution_name))
    if branch["needs_review"]:
        review_reasons.append(
            f"This degree's subject could not be read from the CV, so it "
            f"was counted at full branch relevance. Please confirm it fits "
            f"the {department.upper()} post.")

    note = (f"{equivalence_label} (x{ratio}); "
            f"{tier['tier'].replace('_', ' ')} institution "
            f"(x{tier['multiplier']}); "
            f"branch {branch['verdict']} (x{branch['ratio']})")
    if conferred_by:
        note = (f"conferred by an integrated/dual programme recorded under "
                f"{conferred_by}; " + note)

    return Criterion(
        name=level,
        weight=weight,
        raw=round(fraction, 4),
        unit="fraction of the category earned",
        fraction=fraction,
        points=weight * fraction,
        sources=[f"{level}_degree", f"{level}_institution",
                 f"{level}_subject"],
        evidence=[{
            "qualification": name,
            "organisation": institution_name,
            "subject": subject,
            "year": year,
            "reason": note,
        }],
        insufficient_evidence=False,
        note=note,
        factors=[
            {"factor": "equivalence", "value": ratio,
             "label": equivalence_label,
             "reason": f"{name} counts as {equivalence_label}"},
            {"factor": "institution", "value": tier["multiplier"],
             "tier": tier["tier"], "label": tier["label"],
             "institution": institution_name, "reason": tier["reason"]},
            {"factor": "branch", "value": branch["ratio"],
             "verdict": branch["verdict"], "family": branch["family"],
             "reason": branch["reason"]},
        ],
        needs_review=bool(review_reasons),
        review_reasons=review_reasons,
    )


def score_visibility(verified: list[dict]) -> Criterion:
    """Citations, counted ONLY on corroborated entries."""
    weight = WEIGHTS["research_visibility"]
    citations, rows = 0, []

    for entry in verified:
        if entry.get("credit_exclusions"):
            continue
        verification = entry["verification"]
        if verification["outcome"] not in ("matched", "matched_by_biblio"):
            continue
        count = verification.get("cited_by_count") or 0
        citations += count
        if count:
            rows.append({"title": entry.get("title"), "cited_by": count,
                         "openalex": verification.get("openalex_id")})

    fraction = _capped(citations, FULL_MARKS_CITATIONS)
    return Criterion(
        name="research_visibility",
        weight=weight,
        raw=citations,
        unit="citations of corroborated publications",
        fraction=fraction,
        points=weight * fraction,
        sources=["verification.cited_by_count"],
        evidence=rows,
        insufficient_evidence=(citations == 0),
        note=("no citations on corroborated work - insufficient evidence"
              if citations == 0 else
              f"full marks at {FULL_MARKS_CITATIONS} citations"),
    )


# ---------------------------------------------------------------------------
# Eligibility gate and reviewer flags
# ---------------------------------------------------------------------------

def _value(profile: dict, key: str):
    entry = profile.get(key) or {}
    return entry.get("value") if isinstance(entry, dict) else None


def _evidence(profile: dict, key: str) -> str:
    entry = profile.get(key) or {}
    return entry.get("evidence", "") if isinstance(entry, dict) else ""


def _rejected(profile: dict, key: str):
    entry = profile.get(key) or {}
    return entry.get("rejected_value") if isinstance(entry, dict) else None


def evaluate_gate(profile: dict, summary: dict) -> dict:
    """
    schema.ELIGIBILITY_GATES[0]: doctorate awarded OR NET/SET qualified.

    Produces no number and reads no score.
    """
    phd_status = _value(profile, "phd_status")
    phd_year = _value(profile, "phd_year")
    net = _value(profile, "net_qualified")
    cross_check_failed = bool(summary.get("doctorate_cross_check_failed"))

    # A submitted thesis is evidence of a doctorate in progress, so it
    # lands on 'indeterminate' below. It must NEVER satisfy the 'met'
    # branch: the award is not conferred, and a gate that manufactured a
    # qualification would be the worst error this system could make.
    doctorate_evidenced = bool(
        _value(profile, "highest_degree") in ("phd", "phd_submitted",
                                               "phd_pursuing")
        or phd_status == "thesis_submitted"
        or _value(profile, "phd_institution")
        or cross_check_failed
        or _rejected(profile, "phd_status")
    )

    if phd_status == "awarded" and phd_year:
        outcome = "met"
        reason = f"doctorate awarded {phd_year}"
        evidence = _evidence(profile, "phd_year")
    elif net is True:
        outcome = "met"
        reason = "NET/SET qualified (recognised teaching-eligibility route)"
        evidence = _evidence(profile, "net_qualified")
    elif doctorate_evidenced:
        outcome = "indeterminate"
        if phd_status == "thesis_submitted":
            reason = ("doctoral thesis submitted, award not yet conferred - "
                      "eligible for review, not yet a PhD holder")
        else:
            reason = ("doctorate evidenced but its award is not confirmed"
                      + (" - Stage 4 withheld the claim" if cross_check_failed
                         else " (no award year stated)"))
        evidence = _evidence(profile, "phd_status")
    else:
        outcome = "not_met"
        reason = "no doctorate evidenced and no NET/SET qualification"
        evidence = ""

    return {
        "gate": schema.ELIGIBILITY_GATES[0]["id"],
        "requirement": schema.ELIGIBILITY_GATES[0]["requirement"],
        "outcome": outcome,
        "reason": reason,
        "evidence": evidence,
        "rubric": "DEMO/RESEARCH - not LNMIIT hiring policy",
        "affects_score": False,
        "human_override_allowed": True,
    }


# Roles whose overlap with a degree is normal and must NOT be flagged.
# Shared with Stage 4 via roles.py.
NORMAL_CONCURRENT_ROLE = roles.NORMAL_CONCURRENT_ROLE


def reviewer_flags(profile: dict, as_of: date) -> list[dict]:
    """
    Advisory observations. They change NO score and NO gate outcome.
    """
    flags = []
    phd_start = parse_date(_value(profile, "phd_start_year"))

    # ONLY the doctorate, and only when its start year is explicitly stated.
    # An earlier version also flagged overlaps with an INFERRED master's
    # window (completion year minus two). That was a guess, and guessing is
    # what this project refuses to do - it produced a false flag on
    # Candidate_01 off the back of a separately-wrong masters_year. If the
    # CV does not state when a degree began, we do not pretend to know.
    enrolments = []
    if phd_start:
        phd_end = parse_date(_value(profile, "phd_year")) or (as_of.year, as_of.month)
        enrolments.append(("doctorate", _months(phd_start), _months(phd_end)))

    for position in profile.get("positions") or []:
        title = position.get("title") or ""
        # The section a role came from is as telling as its title. A course
        # listed under "Teaching Assistantship" has a title like
        # "PH-204: Physics Lab IV", which matches no role keyword at all -
        # so checking the title alone flagged a PhD student for the
        # teaching every PhD student does.
        context = f"{title} {position.get('source_section') or ''}"
        if NORMAL_CONCURRENT_ROLE.search(context):
            continue

        start = parse_date(position.get("start_date"))
        if start is None:
            continue
        end = ((as_of.year, as_of.month) if position.get("is_current")
               or not position.get("end_date")
               else parse_date(position.get("end_date")))
        if end is None:
            continue

        employer = position.get("organisation") or "an unnamed employer"
        until = position.get("end_date") or "present"

        for label, enrol_start, enrol_end in enrolments:
            if _months(start) < enrol_end and _months(end) > enrol_start:
                flags.append({
                    "id": "concurrent_employment_and_study",
                    "message": (
                        f"'{title}' at {employer} "
                        f"({position.get('start_date')} to {until}) overlaps "
                        f"the {label} enrolment - please check. This is often "
                        f"entirely legitimate (distance or part-time study, "
                        f"study leave, or rounded CV dates)."),
                    "affects_score": False,
                    "affects_gate": False,
                    "evidence": position.get("evidence", ""),
                })
                break
    return flags


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------

def score_candidate(validated: dict, verified: dict,
                    as_of: date | None = None,
                    department: str = degrees.DEFAULT_DEPARTMENT) -> dict:
    """
    Score one candidate. Deterministic given the four arguments.

    `as_of` is recorded in the output: ongoing roles are measured against
    it, so a result can be reproduced exactly at any later date.

    `department` is the advertised post's discipline. It affects branch
    relevance on the two degree categories and nothing else, and it is
    recorded in the rubric block so a score can never be reproduced
    against a different department by accident.
    """
    as_of = as_of or date.today()
    profile = validated.get("profile", {})
    summary = validated.get("validation_summary", {})
    pubs = verified.get("publications", [])
    patents = validated.get("patents", []) or []
    positions = profile.get("positions") or []

    # Branch relevance may be LIFTED by documented research in the
    # department's field, so the research record is gathered first and
    # handed to the degree criteria as supporting evidence. It is read
    # only - nothing here can change a publication's credit.
    research_context = " ".join(filter(None, [
        str(_value(profile, "research_area") or ""),
        *(str(p.get("title") or "") for p in pubs),
    ]))

    criteria = [
        # --- Research and related contributions: 40 -----------------------
        score_publications(pubs, patents),
        score_visibility(pubs),
        # --- Experience: 25 -----------------------------------------------
        score_experience(positions, {"teaching"}, "teaching_experience",
                         FULL_MARKS_TEACHING_YEARS, as_of),
        score_experience(positions, {"industry"}, "industry_experience",
                         FULL_MARKS_INDUSTRY_YEARS, as_of),
        # --- Education: 35 -------------------------------------------------
        score_doctorate(profile, {}),
        score_postdoctoral(positions, profile, as_of),
        _score_degree(profile, "masters", research_context, department),
        _score_degree(profile, "bachelors", research_context, department),
    ]

    for criterion in criteria:
        criterion.category = next(
            name for name, keys in CATEGORIES.items()
            if criterion.name in keys)

    total = round(sum(c.points for c in criteria), 2)
    category_scores = {
        name: {
            "earned": round(sum(c.points for c in criteria
                                if c.category == name), 2),
            "available": CATEGORY_TOTALS[name],
        }
        for name in CATEGORIES
    }

    # A score built on lookups that never happened is not a score. It is
    # surfaced at the top level, not buried in a criterion note, because
    # the ranking is what people read.
    unreliable = [
        (f"{c.name}: {c.indeterminate_entries} entr(ies) could not be "
         f"verified because the lookup service was unreachable")
        for c in criteria if c.indeterminate
    ]

    return {
        "candidate_id": validated.get("candidate_id"),
        "candidate_name": _value(profile, "full_name"),
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of_date": as_of.isoformat(),
        "rubric": {
            "version": RUBRIC_VERSION,
            "weights": dict(WEIGHTS),
            "categories": {name: list(keys)
                           for name, keys in CATEGORIES.items()},
            "category_totals": dict(CATEGORY_TOTALS),
            "department": department,
            "institution_config": institutions.config()["version"],
            "disclaimer": ("DEMO/RESEARCH rubric for a student project. NOT "
                           "LNMIIT hiring criteria."),
        },
        "eligibility": evaluate_gate(profile, summary),
        "total_score": total,
        "category_scores": category_scores,
        "max_score": sum(WEIGHTS.values()),
        "scoring_reliable": not unreliable,
        "unreliable_reasons": unreliable,
        "criteria": [c.as_dict() for c in criteria],
        "reviewer_flags": reviewer_flags(profile, as_of),
        "human_decision_required": True,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    out_dir = Path(__file__).resolve().parent.parent / "output"
    as_of = date.today()
    for arg in argv:
        if arg.startswith("--as-of="):
            as_of = date.fromisoformat(arg.split("=", 1)[1])

    files = sorted(out_dir.glob("Candidate_*.validated.json"))
    if not files:
        print(f"No *.validated.json in {out_dir}. Run validate.py first.")
        return 1

    print(f"Stage 6: deterministic scoring  (rubric {RUBRIC_VERSION}, "
          f"as-of {as_of})")
    print("DEMO/RESEARCH rubric - NOT LNMIIT hiring criteria\n")

    results = []
    for path in files:
        stem = path.name.replace(".validated.json", "")
        verified_path = out_dir / f"{stem}.verified.json"
        if not verified_path.is_file():
            print(f"{stem}: missing {verified_path.name}")
            continue

        result = score_candidate(
            json.loads(path.read_text(encoding="utf-8")),
            json.loads(verified_path.read_text(encoding="utf-8")),
            as_of=as_of,
        )
        (out_dir / f"{stem}.scored.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        results.append(result)

    eligible = [r for r in results if r["eligibility"]["outcome"] == "met"]
    gated = [r for r in results if r["eligibility"]["outcome"] != "met"]

    for label, group in (("ELIGIBLE", eligible), ("NEEDS REVIEW / NOT ELIGIBLE", gated)):
        print(f"--- {label} " + "-" * (58 - len(label)))
        for r in sorted(group, key=lambda x: -x["total_score"]):
            print(f"  {r['candidate_id']:<14} {r['total_score']:>6.2f}/100   "
                  f"{r['eligibility']['outcome']:<14} {r['eligibility']['reason'][:38]}")
            for c in r["criteria"]:
                marker = "  (insufficient evidence)" if c["insufficient_evidence"] else ""
                print(f"      {c['criterion']:<22} {c['points']:>6.2f} / {c['weight']:<3}"
                      f"  [{c['measured']} {c['unit']}]{marker}")
            for f in r["reviewer_flags"]:
                print(f"      FLAG  {f['message'][:72]}")
        print()

    print(f"Wrote *.scored.json to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
