"""
A short profile summary, assembled from evidence the pipeline already has.

WHY THERE IS NO MODEL CALL HERE
    A summary is the one output that could be written by a model and
    quietly get something wrong - and getting it wrong means telling a
    professor a candidate holds a degree they do not hold. Every
    sentence below is assembled from validated fields by Python, so the
    summary cannot say anything the record does not already contain.
    There is no hallucination to guard against because nothing is
    generated. It also costs nothing: the measured cost is microseconds
    against the minutes an extra Ollama call would add to every upload.

WHAT IT MAY AND MAY NOT SAY
    MAY  - qualifications, institutions, dates, durations, counts and
           research areas, each read from a validated field.
    MUST - distinguish a completed qualification from one in progress.
           "Holds a PhD" and "thesis submitted" are different facts.
    MAY NOT - judge. No "strong", "excellent" or "well suited": ranking
           is the score's job and appointability is the reviewer's.
    MAY NOT - fill a gap. A missing field produces a shorter summary, or
           a plain statement that the CV does not say.

BUILT AT READ TIME, NOT AT UPLOAD TIME
    store.candidate_detail() calls this against the stored audit record,
    which already holds the validated profile, the scored criteria and
    the verification results. That means a candidate scored before this
    module existed gets a summary without re-running the pipeline, and
    rewording a sentence never costs an LLM run. The audit record is
    still the whole input, so the summary remains reproducible from it.
"""

from __future__ import annotations

import degrees
import scoring

MAX_SENTENCES = 4

# An "organisation" longer than this is almost never an employer name.
# CVs list a course title, a programme description or a full postal
# address in that field, and dropping the tail keeps the sentence
# readable without inventing a shorter name.
MAX_ORGANISATION_CHARS = 48


def _organisation(name: str | None) -> str | None:
    """An employer name short enough to sit inside a sentence."""
    if not name:
        return None
    cleaned = " ".join(str(name).split())
    if len(cleaned) <= MAX_ORGANISATION_CHARS:
        return cleaned
    # Cut at a comma where there is one: "X Department, Y University,
    # Pilani, India" keeps "X Department".
    head = cleaned.split(",")[0].strip()
    if 0 < len(head) <= MAX_ORGANISATION_CHARS:
        return head
    # No clean short form. OMIT it rather than print a fragment: the
    # caller drops the clause, and the full name is in the evidence
    # table below. "at Applied Artificial Intelligence for All: Foundat..."
    # is worse than saying nothing.
    return None


def _value(profile: dict, key: str):
    entry = profile.get(key) or {}
    return entry.get("value") if isinstance(entry, dict) else None


def _criterion(scored: dict, name: str) -> dict:
    return next((c for c in scored.get("criteria", [])
                 if c.get("criterion") == name), {})


def _years_phrase(years) -> str | None:
    """'2.5' -> 'two and a half years'. Durations read better as words."""
    try:
        value = float(years)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    whole = int(value)
    months = round((value - whole) * 12)
    if months == 12:
        whole, months = whole + 1, 0
    names = ["", "one", "two", "three", "four", "five", "six", "seven",
             "eight", "nine", "ten", "eleven", "twelve"]

    def word(count):
        return names[count] if 0 < count <= 12 else str(count)

    head = word(whole) if whole else ""
    if whole and months:
        return (f"{head} year{'s' if whole != 1 else ''} and "
                f"{word(months)} month{'s' if months != 1 else ''}")
    if whole:
        return f"{head} year{'s' if whole != 1 else ''}"
    return f"{word(months)} month{'s' if months != 1 else ''}"


def _degree_phrase(profile: dict, level: str) -> str | None:
    """"an M.Tech in Embedded Systems from Mewar University", or None."""
    name = degrees.display_name(_value(profile, f"{level}_degree"))
    if not name:
        return None
    subject = _value(profile, f"{level}_subject")
    institution = _value(profile, f"{level}_institution")
    year = _value(profile, f"{level}_year")

    phrase = f"{name}"
    if subject and subject.lower() not in phrase.lower():
        phrase += f" in {subject}"
    shown = _organisation(institution)
    if shown:
        phrase += f" from {shown}"
    if year:
        phrase += f" ({year})"
    return phrase


def _qualification_sentence(profile: dict) -> tuple[str | None, list[str]]:
    """
    The highest COMPLETED qualification, with anything in progress named
    separately. Conflating the two is the single most misleading thing a
    summary of an academic CV can do.
    """
    missing = []
    status = _value(profile, "phd_status")
    institution = _value(profile, "phd_institution")
    subject = _value(profile, "phd_subject")
    year = _value(profile, "phd_year")

    masters = _degree_phrase(profile, "masters")
    bachelors = _degree_phrase(profile, "bachelors")

    if status == "awarded" and year:
        phrase = "Holds a PhD"
        if subject:
            phrase += f" in {subject}"
        shown = _organisation(institution)
        if shown:
            phrase += f" from {shown}"
        return f"{phrase}, awarded {year}.", missing

    # In progress. State the completed qualification FIRST, so the
    # sentence cannot be skim-read as a doctorate the candidate holds.
    completed = masters or bachelors
    if status in ("thesis_submitted", "pursuing") or institution:
        shown = _organisation(institution)
        where = f" at {shown}" if shown else ""
        stage = (f"has submitted a doctoral thesis{where}, which is awaiting "
                 f"the award" if status == "thesis_submitted"
                 else f"is enrolled for a doctorate{where}")
        if completed:
            return (f"Highest completed qualification is {completed}; the "
                    f"candidate {stage}."), missing
        missing.append("no completed degree is recorded")
        return f"The candidate {stage}.", missing

    if completed:
        return f"Highest completed qualification is {completed}.", missing

    missing.append("the CV records no qualification we could read")
    return None, missing


def _experience_sentence(scored: dict) -> str | None:
    teaching = _criterion(scored, "teaching_experience")
    industry = _criterion(scored, "industry_experience")
    postdoc = _criterion(scored, "postdoctoral")

    parts = []
    taught = _years_phrase(teaching.get("measured"))
    if taught:
        clause = f"{taught} of teaching experience"
        # Name the most recent post, which is what a reviewer looks for
        # first, and take it from the evidence rather than inferring it.
        # Ordered by PARSED date. Sorting the raw strings put "Nov 2025"
        # above "2021" because 'N' sorts after '2', so the "most recent"
        # role was whichever one happened to sort last - a wrong fact
        # stated confidently, which is the worst kind for a summary.
        roles = [r for r in (teaching.get("evidence") or []) if r.get("title")]
        dated = [(scoring.parse_date(r.get("from")), r) for r in roles]
        dated = [(d, r) for d, r in dated if d]
        latest = max(dated, key=lambda pair: pair[0])[1] if dated else None
        if latest:
            shown = _organisation(latest.get("organisation"))
            where = f" at {shown}" if shown else ""
            clause += f", most recently as {latest['title']}{where}"
        parts.append(clause)

    worked = _years_phrase(industry.get("measured"))
    if worked:
        parts.append(f"{worked} in industry")

    research = _years_phrase(postdoc.get("measured"))
    if research:
        parts.append(f"{research} of postdoctoral research")

    if not parts:
        return None
    if len(parts) == 1:
        return f"The CV documents {parts[0]}."
    return f"The CV documents {', and '.join([', '.join(parts[:-1]), parts[-1]])}."


def _research_sentence(profile: dict, scored: dict) -> str | None:
    publications = _criterion(scored, "publications")
    visibility = _criterion(scored, "research_visibility")
    rows = publications.get("evidence") or []
    papers = [r for r in rows if r.get("kind") != "patent"]
    patents = [r for r in rows if r.get("kind") == "patent"]
    corroborated = [r for r in papers
                    if r.get("verification") in ("matched", "matched_by_biblio")]
    granted = [r for r in patents if r.get("status") == "granted"]

    parts = []
    area = _value(profile, "research_area")
    if area:
        parts.append(f"Research is in {area.rstrip('.')}")

    if papers:
        count = (f"{len(papers)} publication"
                 f"{'s' if len(papers) != 1 else ''} listed")
        if corroborated:
            count += (f", {len(corroborated)} of them corroborated against "
                      f"an external index")
        parts.append(count)

    citations = visibility.get("measured")
    if isinstance(citations, (int, float)) and citations > 0:
        parts.append(f"{int(citations)} citations on corroborated work")

    if granted:
        parts.append(f"{len(granted)} granted patent"
                     f"{'s' if len(granted) != 1 else ''}")
    elif patents:
        parts.append(f"{len(patents)} patent claim"
                     f"{'s' if len(patents) != 1 else ''} not yet granted")

    if not parts:
        return None
    return ". ".join([parts[0]]) + (
        f", with {', '.join(parts[1:])}." if len(parts) > 1 else ".")


def _other_sentence(profile: dict, scored: dict) -> str | None:
    """Only facts that change how a reviewer reads the rest."""
    notes = []
    if _value(profile, "net_qualified") is True:
        notes.append("has passed a national teaching-eligibility examination "
                     "(NET/SET)")
    if not scored.get("scoring_reliable", True):
        notes.append("part of the record could not be verified when the "
                     "candidate was scored")
    if not notes:
        return None
    return "The candidate " + ", and ".join(notes) + "."


def build_summary(audit: dict) -> dict:
    """
    A 2-4 sentence profile summary, plus what it could not say.

    Returns {"text", "sentences", "unavailable", "generated_by"}. The
    caller renders `text`; `unavailable` exists so a thin summary reads
    as a gap in the CV rather than as a gap in the candidate.
    """
    profile = (audit.get("validation") or {}).get("profile") or {}
    scored = audit.get("scoring") or {}

    qualification, missing = _qualification_sentence(profile)
    sentences = [s for s in (
        qualification,
        _experience_sentence(scored),
        _research_sentence(profile, scored),
        _other_sentence(profile, scored),
    ) if s]

    if not sentences:
        return {
            "text": ("The CV did not provide enough information we could "
                     "read to summarise this candidate. The evidence below "
                     "is what the pipeline was able to extract."),
            "sentences": [],
            "unavailable": missing or ["no readable evidence"],
            "generated_by": "deterministic",
        }

    return {
        "text": " ".join(sentences[:MAX_SENTENCES]),
        "sentences": sentences[:MAX_SENTENCES],
        "unavailable": missing,
        "generated_by": "deterministic",
    }
