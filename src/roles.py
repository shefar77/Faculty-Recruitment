"""
ROLE TAXONOMY - one place that decides what a job title means.

WHY THIS MODULE EXISTS
  The rules for "is this teaching?", "is this a faculty appointment or an
  assistantship?" and "is this employer academic?" used to live as four
  separate regex blocks in validate.py and scoring.py. They were written
  while looking at six CVs, and they encoded those six CVs' vocabulary:
  "Teaching Fellow", "Adjunct", "Sessional" and plain "Instructor" matched
  nothing at all, so entirely ordinary academic appointments fell through
  to "uncertain".

  Consolidating them here is not tidying. Two modules asking the same
  question of the same title and getting different answers is a defect
  waiting to happen, and a taxonomy spread across two files cannot be
  reviewed as a whole by the faculty who have to approve it.

DESIGN RULES
  1. Classify on the HEAD NOUN of the title, not on any word in it.
     "Assistant Professor" is a faculty appointment, not an assistantship,
     because its head noun is "professor". The same rule gets "Research
     Assistant Professor" and the US "Teaching Assistant Professor" right,
     which a keyword search cannot.

  2. Every answer carries its REASON. A classification a reviewer cannot
     interrogate is not evidence, it is an assertion.

  3. "Uncertain" is a real answer. Where the title and its context do not
     settle the question, say so and let a human decide, rather than
     guessing and presenting the guess as a finding.

  4. No rule may reference a candidate, a CV, an employer or a person.
     Rules are about vocabulary. Tests may use specific examples; this
     module may not.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 1. Faculty appointments - independent responsibility for a course
# ---------------------------------------------------------------------------
# Matched on the head noun, so any modifier ("Assistant", "Associate",
# "Visiting", "Adjunct", "Clinical", "Research") is carried along without
# changing the answer.
FACULTY_HEAD = re.compile(
    r"\b("
    r"professor|prof\.?"
    r"|lecturer"
    r"|reader"
    r"|tenure[- ]track"
    r"|faculty member|faculty"
    r"|instructor"
    r"|teaching fellow"
    r"|preceptor"
    r"|module (?:leader|convenor|convener|coordinator)"
    r"|course (?:leader|convenor|convener|coordinator|director)"
    r"|head of (?:department|school|faculty)"
    r"|dean|principal|provost|rector"
    r"|academic coordinator"
    r")\b",
    re.I,
)

# Titles that are assistive teaching: supervised, course-support, usually
# held alongside study. Deliberately checked AFTER the faculty head noun.
ASSISTANTSHIP_HEAD = re.compile(
    r"\b("
    r"teaching assistant|graduate teaching assistant|gta|\bta\b"
    r"|teaching assistantship|assistantship"
    r"|lab(?:oratory)? (?:instructor|assistant|demonstrator|tutor)"
    r"|demonstrator"
    r"|tutor|tutorial assistant"
    r"|grader|marker"
    r"|student assistant"
    r"|peer (?:tutor|mentor)"
    r")\b",
    re.I,
)

# Research roles. Not teaching, but academic - they must not be swept into
# "industry" merely because the employer string lacks a university word.
RESEARCH_HEAD = re.compile(
    r"\b("
    r"research (?:assistant|associate|fellow|scholar|officer|scientist|engineer)"
    r"|postdoc(?:toral)?(?: (?:fellow|researcher|associate|scholar))?"
    r"|post[- ]doc(?:toral)?"
    r"|scientist|senior scientist|principal investigator|\bpi\b"
    r"|doctoral (?:scholar|fellow|researcher|candidate|student)"
    r"|phd (?:scholar|student|researcher|candidate)"
    r"|\bjrf\b|\bsrf\b|junior research fellow|senior research fellow"
    r"|project (?:associate|assistant|fellow|scientist)"
    r"|research intern"
    r")\b",
    re.I,
)

# Research titles that belong to academic training whoever funds them.
ACADEMIC_TRAINEE = re.compile(
    r"\b(postdoc\w*|post[- ]doc\w*|doctoral|phd|ph\.d|jrf|srf"
    r"|junior research fellow|senior research fellow|research scholar)\b",
    re.I)

# ---------------------------------------------------------------------------
# 2. Employers
# ---------------------------------------------------------------------------
# Signals that an employer is an academic or public research body. Kept
# broad on purpose: the cost of missing one is misfiling a research post as
# industry, which moves points between criteria.
#
# A name alone cannot settle this. Plenty of genuine research institutes
# carry no academic word ("Bell Labs", and a great many national bodies
# known only by an acronym), so a negative result here is treated as
# "no academic signal in the NAME", never as proof of a commercial employer.
ACADEMIC_EMPLOYER = re.compile(
    r"("
    r"universit|college|school of|\bschool\b|faculty of|department of"
    r"|institute|institut|\binst\.?\b"
    r"|polytechnic|academy|akademi"
    r"|\biit\b|\bnit\b|\biiit\b|\biisc\b|\biiser\b|\bnist\b|\bisi\b"
    r"|\baiims\b|\bicar\b|\bcsir\b|\bdrdo\b|\bisro\b|\bbarc\b|\btifr\b"
    r"|vidyalaya|vishwavidyalaya|mahavidyalaya|gurukul"
    r"|research (?:centre|center|institute|laborator|station|council)"
    r"|national laborator|laborator"
    r"|observatory|herbarium"
    r"|\bcnrs\b|\bmax planck\b|\beth\b|\bmit\b"
    r")",
    re.I,
)

# Words that mark a commercial entity. Used only as a POSITIVE signal for
# industry, never to override an academic signal - plenty of universities
# are incorporated ("... University Pvt. Ltd.").
COMMERCIAL_EMPLOYER = re.compile(
    r"("
    r"\bpvt\.?\b|\bprivate limited\b|\bltd\.?\b|\bllp\b|\binc\.?\b"
    r"|\bcorp\.?\b|\bcorporation\b|\bgmbh\b|\bplc\b|\bco\.\b"
    r"|technologies|technology solutions|software|solutions|systems"
    r"|consultanc|services|labs\b|startup"
    r")",
    re.I,
)

# Roles whose overlap with a degree is entirely normal and must never be
# raised as a concurrency flag.
NORMAL_CONCURRENT_ROLE = re.compile(
    r"\b("
    r"teaching assistant|research assistant|assistantship"
    r"|research scholar|doctoral (?:scholar|fellow|candidate|student)"
    r"|phd (?:scholar|student|candidate)"
    r"|\bjrf\b|\bsrf\b|research fellow|project (?:associate|assistant|fellow)"
    r"|graduate assistant|student assistant|demonstrator|tutor"
    r"|intern(?:ship)?|trainee"
    r")\b",
    re.I,
)

# Roles that are training, not employment. These are not positions and must
# not earn experience credit.
TRAINING_ONLY = re.compile(
    r"\b("
    r"workshop|seminar|webinar|bootcamp|short[- ]term course"
    r"|certification|certificate course|training programme|training program"
    r"|summer school|winter school|\bfdp\b|faculty development"
    r")\b",
    re.I,
)


# ---------------------------------------------------------------------------
# 3. Classification
# ---------------------------------------------------------------------------

def _context(position: dict) -> str:
    """
    Title plus the CV section it was printed under.

    The heading carries real information the title often lacks: a course
    code listed under a "Teaching Assistantship" heading has a title that
    matches no role vocabulary at all. Reading both is what makes the
    classifier work on CVs that organise by section rather than by title.
    """
    return f"{position.get('title') or ''} {position.get('source_section') or ''}"


def teaching_kind(position: dict) -> tuple[str, str]:
    """
    Is a teaching role a faculty APPOINTMENT or an ASSISTANTSHIP?

    Returns (kind, reason) where kind is
    'appointment' | 'assistantship' | 'uncertain'.

    The head-noun rule decides it: a title headed by professor, lecturer,
    reader or instructor is an appointment whatever modifies it. Only when
    no faculty head noun is present does an assistantship keyword settle
    the question.
    """
    title = position.get("title") or ""
    context = _context(position)

    faculty = FACULTY_HEAD.search(title) or FACULTY_HEAD.search(context)
    assistant = ASSISTANTSHIP_HEAD.search(title)

    if faculty and not assistant:
        return "appointment", (
            f"title is headed by {faculty.group(0)!r}, which denotes an "
            f"appointment with independent teaching responsibility")

    if faculty and assistant:
        title_faculty = FACULTY_HEAD.search(title)

        if title_faculty:
            # Both words are in the TITLE. Which reading is right depends
            # on whether an assistantship phrase SUBSUMES the faculty word
            # or merely sits beside it:
            #
            #   "Lab Instructor"               -> "Lab Instructor" contains
            #       "Instructor". The qualifier narrows the faculty word to
            #       course support, so the longer phrase is the real title.
            #
            #   "Teaching Assistant Professor" -> "Teaching Assistant" and
            #       "Professor" are disjoint. "Professor" is the head noun
            #       and the rest modifies it, so it is a faculty post.
            #
            # Length alone cannot tell these apart - the assistantship
            # match is longer in both - but containment can.
            #
            # EVERY assistantship phrase has to be checked, not just the
            # first. A combined title like "Teaching Assistant / Lab
            # Instructor" holds two of them, and the faculty word sits
            # inside the SECOND; comparing against the first alone read it
            # as a professorship and awarded a doctoral student full
            # faculty credit.
            enclosing = next(
                (m for m in ASSISTANTSHIP_HEAD.finditer(title)
                 if m.start() <= title_faculty.start()
                 and m.end() >= title_faculty.end()),
                None)
            if enclosing:
                return "assistantship", (
                    f"title is {enclosing.group(0)!r}; the qualifier narrows "
                    f"{title_faculty.group(0)!r} to course-support teaching")
            return "appointment", (
                f"title contains {assistant.group(0)!r} but is headed by "
                f"{title_faculty.group(0)!r}; the head noun determines the post")

        # Faculty word came only from the SECTION heading, assistantship
        # from the title itself. The title is the stronger signal.
        return "assistantship", (
            f"title is {assistant.group(0)!r}; the faculty wording appears "
            f"only in the section heading")

    if assistant:
        return "assistantship", (
            f"title is headed by {assistant.group(0)!r}, which denotes "
            f"course-support teaching under supervision")

    # Nothing in the title. The section heading is the last resort.
    section_assistant = ASSISTANTSHIP_HEAD.search(
        position.get("source_section") or "")
    if section_assistant:
        return "assistantship", (
            f"title matches no role vocabulary, but it is listed under a "
            f"{section_assistant.group(0)!r} heading")

    return "uncertain", (
        "neither the role title nor its section heading identifies this as "
        "a faculty appointment or an assistantship")


def employer_kind(organisation: str | None) -> tuple[str, str]:
    """
    'academic' | 'commercial' | 'unknown', with the reason.

    'unknown' is distinct from 'commercial' and that distinction matters:
    an employer whose NAME carries no academic word is not thereby a
    company. Many research bodies are known only by an acronym.
    """
    name = (organisation or "").strip()
    if not name:
        return "unknown", "no employer named in the CV"

    academic = ACADEMIC_EMPLOYER.search(name)
    commercial = COMMERCIAL_EMPLOYER.search(name)

    if academic:
        return "academic", f"employer name contains {academic.group(0)!r}"
    if commercial:
        return "commercial", f"employer name contains {commercial.group(0)!r}"
    return "unknown", (
        "employer name carries no academic or commercial marker, so its "
        "nature cannot be determined from the CV alone")


def classify_position(position: dict) -> dict:
    """
    Derive a position's CATEGORY from its title, section and employer.

    Returns {"category", "confidence", "reason", "needs_review"}.
    Category is one of schema.POSITION_CATEGORIES.

    PRECEDENCE, and why:
      1. Training entries are not employment at all.
      2. A role title that names a teaching post is teaching, whoever the
         employer is - a lecturer at a corporate academy still teaches.
      3. A research title is research, for the same reason. This is the
         rule that stops a research post at an acronym-named institute
         being filed as industry.
      4. Only once the title says nothing does the employer decide.
      5. An unknown employer with an uninformative title is 'other' and
         FLAGGED, never guessed.
    """
    title = position.get("title") or ""
    context = _context(position)
    organisation = position.get("organisation")
    employer, employer_reason = employer_kind(organisation)

    if TRAINING_ONLY.search(context):
        return {
            "category": "other",
            "confidence": "high",
            "reason": "this is training or a short course, not employment",
            "needs_review": False,
        }

    if FACULTY_HEAD.search(context) or ASSISTANTSHIP_HEAD.search(context):
        kind, reason = teaching_kind(position)
        return {
            "category": "teaching",
            "confidence": "high",
            "reason": f"teaching role: {reason}",
            "needs_review": kind == "uncertain",
        }

    if RESEARCH_HEAD.search(context):
        match = RESEARCH_HEAD.search(context)
        # A research post at an employer that is positively commercial
        # ("... Private Limited") and shows no academic signal is
        # industry experience - schema.POSITION_CATEGORIES defines
        # industry as any non-academic employer. Doctoral and postdoctoral
        # titles are academic by nature and are never moved.
        if employer == "commercial" and not ACADEMIC_TRAINEE.search(context):
            return {
                "category": "industry",
                "confidence": "high",
                "reason": (f"research role ({match.group(0)!r}) at a "
                           f"commercial employer: {employer_reason}"),
                "needs_review": False,
            }
        return {
            "category": "research",
            "confidence": "high",
            "reason": f"research role: title contains {match.group(0)!r}",
            "needs_review": False,
        }

    # The title is uninformative. Fall back to the employer.
    if employer == "commercial":
        return {
            "category": "industry",
            "confidence": "medium",
            "reason": (f"title names no academic role; {employer_reason}"),
            "needs_review": False,
        }

    if employer == "academic":
        return {
            "category": "other",
            "confidence": "low",
            "reason": (f"{employer_reason}, but the title names no teaching "
                       f"or research role, so the nature of the post is "
                       f"unclear"),
            "needs_review": True,
        }

    return {
        "category": "other",
        "confidence": "low",
        "reason": (f"{employer_reason}; the title names no academic role "
                   f"either, so this post cannot be categorised from the CV"),
        "needs_review": True,
    }


# ---------------------------------------------------------------------------
# 4. ACADEMIC RANK
# ---------------------------------------------------------------------------
# Until now this module deliberately DISCARDED the modifier on a faculty
# title: "Assistant", "Associate" and "Visiting" all collapsed to
# "appointment", because the only question being asked was whether the
# post carried independent teaching responsibility.
#
# The revised rubric asks a second, different question - how senior the
# post was - so the modifier is now read as well. The two answers stay
# separate: teaching_kind() still decides whether a role counts at all,
# and faculty_rank() only scales what it is worth. A rank is never
# allowed to turn an assistantship into an appointment.
#
# DEMO/RESEARCH multipliers. They need faculty approval.
RANK_CREDIT = {
    "professor": 1.30,
    "associate_professor": 1.15,
    "assistant_professor": 1.00,
    "faculty": 1.00,          # a teaching post whose rank is not stated
}

# A junior modifier sitting immediately before the professor head noun.
# Checked BEFORE the bare head noun, because "Assistant Professor"
# contains "Professor" and the longer reading is the correct one.
_ASSISTANT_PROF = re.compile(
    r"\b(assistant|asst\.?|asstt\.?)\s+(?:prof(?:essor)?\.?)\b", re.I)
_ASSOCIATE_PROF = re.compile(
    r"\b(associate|assoc\.?)\s+(?:prof(?:essor)?\.?)\b", re.I)
# Reader (UK/Commonwealth) and Senior Lecturer sit at associate level.
_ASSOCIATE_EQUIVALENT = re.compile(r"\b(reader|senior lecturer)\b", re.I)
# Modifiers that describe the CONTRACT, not the rank. A visiting or
# adjunct professorship may be held by anyone, so it earns the base rate
# rather than the full-professor rate.
_UNRANKED_MODIFIER = re.compile(
    r"\b(visiting|adjunct|guest|honorary|emeritus|sessional|contract"
    r"|part[- ]time|research)\s+(?:prof(?:essor)?\.?)\b", re.I)
_BARE_PROFESSOR = re.compile(r"\b(?:full\s+)?prof(?:essor)?\.?\b", re.I)


def faculty_rank(position: dict) -> tuple[str, float, str]:
    """
    Seniority of a faculty appointment: (rank, credit_per_month, reason).

    Only meaningful for roles teaching_kind() already called an
    'appointment'. Assistantships do not have a rank and must not be
    passed here.

    PRECEDENCE, and why each step is before the next:
      1. "Assistant Professor" - the junior modifier binds to the head
         noun, so the longer phrase is the real title.
      2. "Associate Professor", and Reader / Senior Lecturer, which are
         the same level under a different national convention.
      3. A contract modifier ("Visiting", "Adjunct") describes the terms
         of the appointment, not its seniority, so it earns the base rate
         rather than being read as a full professorship.
      4. A bare "Professor" is a full professorship.
      5. Anything else is a teaching post of unstated rank, at base rate.
    """
    title = position.get("title") or ""

    # Contract guard. An assistantship has no academic rank, and calling
    # this on one would quietly return the base faculty rate - turning a
    # half-rate TA-ship into full-rate teaching. teaching_kind() is the
    # only thing allowed to decide that, so disagreeing with it here is a
    # programming error rather than a scoring judgement.
    if teaching_kind(position)[0] == "assistantship":
        raise ValueError(
            f"faculty_rank() called on an assistantship ({title!r}); "
            f"rank applies only to roles teaching_kind() calls an "
            f"appointment")

    if _ASSISTANT_PROF.search(title):
        return ("assistant_professor", RANK_CREDIT["assistant_professor"],
                "Assistant Professor - entry-grade faculty appointment")

    if _ASSOCIATE_PROF.search(title):
        return ("associate_professor", RANK_CREDIT["associate_professor"],
                "Associate Professor - mid-grade faculty appointment")

    equivalent = _ASSOCIATE_EQUIVALENT.search(title)
    if equivalent:
        return ("associate_professor", RANK_CREDIT["associate_professor"],
                f"{equivalent.group(0)} - equivalent to Associate Professor "
                f"under Commonwealth titling")

    contract = _UNRANKED_MODIFIER.search(title)
    if contract:
        return ("faculty", RANK_CREDIT["faculty"],
                f"{contract.group(0)} - the modifier describes the contract "
                f"rather than the rank, so it is counted at the base rate")

    if _BARE_PROFESSOR.search(title):
        return ("professor", RANK_CREDIT["professor"],
                "Professor - senior faculty appointment")

    return ("faculty", RANK_CREDIT["faculty"],
            "a teaching appointment whose rank the CV does not state, "
            "counted at the base rate")


# ---------------------------------------------------------------------------
# 5. POSTDOCTORAL APPOINTMENTS
# ---------------------------------------------------------------------------
# A postdoc is a distinct career stage and the rubric scores it on its own
# line, so it must be told apart from the two things it is most often
# confused with:
#
#   - A PHD ITSELF. "Research Scholar", "Doctoral Fellow" and "JRF/SRF"
#     are the doctorate, not what follows it. RESEARCH_HEAD matches them
#     and this pattern deliberately does not.
#   - ORDINARY RESEARCH EMPLOYMENT. "Research Associate" and "Research
#     Scientist" are real research posts but are held before, during and
#     after a doctorate. Crediting them as postdoctoral experience would
#     award the category to people who never held a postdoc.
#
# So the test is explicit wording only. A post that IS a postdoc but is
# printed under an unusual title scores nothing here - and shows up in the
# research criterion instead, never as a silent zero.
POSTDOC_HEAD = re.compile(
    r"\b(post[\s\-]?doc(?:toral|torate)?"
    r"|postdoctoral (?:fellow|researcher|research associate|associate|scholar|scientist)"
    r"|post[\s\-]doctoral"
    r"|\bpdf\b(?=\s*(?:fellow|position|at|,))"
    r"|national post[\s\-]?doctoral fellow|\bnpdf\b"
    r"|research fellow(?=.*\bpost[\s\-]?doc))\b",
    re.I,
)


def is_postdoc(position: dict) -> tuple[bool, str]:
    """
    Is this position a postdoctoral appointment? (verdict, reason).

    Reads the section heading as well as the title, because CVs routinely
    print a bare institution name under a "Postdoctoral Experience"
    heading and leave the title implicit.
    """
    title = position.get("title") or ""
    match = POSTDOC_HEAD.search(title)
    if match:
        return True, f"title states {match.group(0)!r}"

    section = position.get("source_section") or ""
    section_match = POSTDOC_HEAD.search(section)
    if section_match:
        return True, (f"listed under a {section_match.group(0)!r} section "
                      f"heading")

    return False, "neither the title nor the section heading states a "\
                  "postdoctoral appointment"
