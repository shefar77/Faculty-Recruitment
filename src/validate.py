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

# How close a string must be to count as "present in the source".
# 1.0 would fail on a single stray space; this tolerates whitespace and
# punctuation noise while still catching invented content.
FUZZY_THRESHOLD = 0.90

FREE_TEXT_FIELDS = (
    "full_name", "email", "orcid", "scholar_url",
    "phd_institution", "masters_degree", "masters_institution",
    "research_area",
)
NUMERIC_FIELDS = ("phd_start_year", "phd_year", "masters_year")
CLASSIFICATION_FIELDS = ("highest_degree", "phd_status")
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
# So Python cross-checks the claim against the rest of the CV. The guard
# only ever moves an answer toward "needs a human" - it can withhold an
# unsupported "awarded", never manufacture one.

PHD_IN_PROGRESS_HINT = re.compile(
    "(research scholar|phd scholar|ph d scholar|doctoral scholar|"
    "pursuing|ongoing|thesis submitted|under submission|"
    "perusing)"
)

# A doctorate awarded within two years of starting is not credible; it
# almost always means a start year was misread as a completion year.
MIN_PLAUSIBLE_PHD_YEARS = 2


COMPLETION_YEAR_FIELDS = ("masters_year", "phd_year")
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


def cross_check_doctorate(results: list[dict], source_norm: str) -> list[dict]:
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
    if PHD_IN_PROGRESS_HINT.search(source_norm):
        reasons.append("the CV describes the doctorate as in progress "
                       "(e.g. 'Research Scholar' / 'Present')")

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


def appears_in(needle: str, haystack_norm: str) -> tuple[bool, float]:
    """
    Is `needle` present in the normalised source?

    Exact substring first (the common case). Failing that, slide a window of
    the needle's length over the haystack and take the best similarity - a
    model that drops one word from a long institution name should not be
    branded a fabricator.
    """
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

def check_scalar(name: str, wrapper, source_norm: str) -> dict:
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
            mentioned = DOCTORATE_HINT.search(source_norm) is not None
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
    val_ok, val_score = appears_in(value, source_norm)
    result["value_in_source"] = val_ok
    result["value_score"] = val_score
    result["status"] = "verified" if val_ok else "unverified"
    result["detail"] = ("" if val_ok
                        else f"value not found in the CV (best match {val_score})")
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


def check_publication(index: int, pub: dict, source_norm: str) -> dict:
    """Validate one publication entry and apply the status guard."""
    issues: list[str] = []
    flags: list[str] = []
    title = pub.get("title") or ""
    evidence = pub.get("evidence") or ""

    title_ok, title_score = appears_in(title, source_norm) if title else (False, 0.0)
    ev_ok, _ = appears_in(evidence, source_norm) if evidence.strip() else (False, 0.0)

    if not title_ok:
        issues.append(f"title not found in CV (best {title_score})")
    if not ev_ok:
        issues.append("evidence span not found in CV")

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
    declared = pub.get("status") or "unknown"
    guard = None
    if declared != "unknown" and not STATUS_MARKER.search(evidence):
        guard = {"from": declared, "to": "unknown",
                 "reason": "no status marker present in the evidence span"}
        declared = "unknown"

    if THESIS_HINT.search(evidence) or THESIS_HINT.search(title):
        flags.append("evidence mentions a thesis/dissertation - may not be a "
                     "publication (reviewer judgement)")

    return {
        "index": index,
        "title": title,
        "status_final": declared,
        "status_downgraded": guard,
        "title_in_source": title_ok,
        "evidence_in_source": ev_ok,
        "unsupported_authors": unsupported,
        "issues": issues,
        "flags": flags,
        "verdict": "verified" if not issues else "unverified",
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

ACADEMIC_EMPLOYER = re.compile(
    r"(universit|college|institute of technology|\binstitute\b|\biit\b|\bnit\b|"
    r"\biiit\b|\biisc\b|polytechnic|academy|\bschool\b|vidyalaya|vishwavidyalaya)",
    re.I)

TEACHING_TITLE = re.compile(
    r"(professor|lecturer|reader|faculty|teaching assistant|instructor|"
    r"tutor|demonstrator|assistantship)", re.I)

RESEARCH_TITLE = re.compile(
    r"(research|postdoc|post-doc|scientist|scholar|fellow|jrf|srf)", re.I)


def review_position_categories(positions: list) -> tuple[list, list]:
    """
    Re-derive each position's category from the schema's definitions.

    Only CHANGES a category when the evidence settles it: an employer is
    named, and the employer's nature plus the role title point one way.
    Where the CV names no employer and the model was unsure, the entry is
    left alone and flagged, because guessing is what this system is built
    not to do.
    """
    notes = []
    for index, position in enumerate(positions):
        title = position.get("title") or ""
        organisation = position.get("organisation") or ""
        proposed = (position.get("category") or "other").lower()

        if not organisation.strip():
            if proposed == "other":
                notes.append(
                    f"position[{index}] '{title}' has no employer named and "
                    f"could not be categorised - counted toward no experience "
                    f"criterion; please check")
            continue

        academic = bool(ACADEMIC_EMPLOYER.search(organisation))
        if academic:
            if TEACHING_TITLE.search(title):
                derived = "teaching"
            elif RESEARCH_TITLE.search(title):
                derived = "research"
            else:
                derived = proposed if proposed != "other" else "other"
        else:
            # schema.py: industry is "any non-academic employer".
            derived = "industry"

        if derived != proposed:
            position["category"] = derived
            position["category_proposed_by_model"] = proposed
            position["category_rule"] = (
                f"employer {organisation!r} is "
                f"{'academic' if academic else 'non-academic'}; "
                f"title {title!r} -> {derived}")
            notes.append(
                f"position[{index}] '{title}' at '{organisation}': category "
                f"{proposed} -> {derived} ({position['category_rule']})")

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

        title_ok, title_score = appears_in(title, source_norm) if title else (False, 0.0)
        if not title_ok:
            issues.append(f"position[{index}] title {title!r} not found in CV "
                          f"(best {title_score})")
            entry["title_unverified"] = True

        if organisation:
            org_ok, org_score = appears_in(organisation, source_norm)
            if not org_ok:
                issues.append(f"position[{index}] organisation {organisation!r} "
                              f"not found in CV (best {org_score})")
                entry["organisation"] = None
                entry["rejected_organisation"] = organisation

        if evidence and not appears_in(evidence, source_norm)[0]:
            entry["evidence_unverified"] = True

        cleaned.append(entry)

    return cleaned, issues


def validate_record(record: dict, source_text: str) -> dict:
    source_norm = normalise(source_text)
    profile = record.get("profile", {})

    scalar_results = [
        check_scalar(name, profile.get(name), source_norm)
        for name in (FREE_TEXT_FIELDS + NUMERIC_FIELDS + CLASSIFICATION_FIELDS
                     + ELIGIBILITY_FLAG_FIELDS)
    ]

    # Order matters: correct completion years BEFORE the doctorate
    # cross-check, which reasons about the start/end year gap.
    year_notes = cross_check_completion_years(scalar_results)
    doctorate_notes = cross_check_doctorate(scalar_results, source_norm)

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

    pub_results = [check_publication(i, p, source_norm)
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

    verified = sum(1 for r in scalar_results if r["status"] == "verified")
    partial = sum(1 for r in scalar_results if r["status"] == "partial")
    present = sum(1 for r in scalar_results if r["status"] != "absent")
    downgraded = sum(1 for r in pub_results if r["status_downgraded"])

    clean["positions"] = clean_positions

    return {
        "candidate_id": record.get("candidate_id"),
        "profile": clean,
        "publications": clean_pubs,
        "validation_summary": {
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
                  + doctorate_notes + year_notes),
        "doctorate_cross_check_failed": bool(doctorate_notes),
        },
        "scalar_detail": scalar_results,
        "publication_detail": pub_results,
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
