"""
Degree taxonomy - level, equivalence to the advertised post, and branch.

THREE INDEPENDENT QUESTIONS, answered separately and never merged
    1. LEVEL       - is this a bachelor's, a master's or a doctorate?
    2. EQUIVALENCE - how close is this qualification to the one the post
                     asks for AT THAT LEVEL? (B.Tech vs B.Sc vs BCA)
    3. BRANCH      - is the SUBJECT relevant to the department?
    A B.Sc in Computer Science and a B.Tech in Civil Engineering fail
    different tests. Collapsing the two into one "relevance" number would
    make the score impossible to explain and would penalise the first
    candidate for something they did not do wrong.

    Institution standing is a fourth question and lives in institutions.py.

NO RULE HERE NAMES A CANDIDATE, A COLLEGE OR A DEMO CV.
    A test asserts it.

DEMO/RESEARCH values throughout. Every ratio below is a proposal for a
student project and needs faculty approval before real use.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 1. LEVEL
# ---------------------------------------------------------------------------
# Matched on the qualification name. Order matters: the doctorate test runs
# before the master's test, because "Ph.D." and "M.Phil" can share a line.

DOCTORATE = re.compile(
    r"\b(ph\s*\.?\s*d|phd|d\s*\.?\s*phil|doctor of philosophy|doctorate"
    r"|dsc|d\s*\.?\s*sc|sc\s*\.?\s*d|eng\s*\.?\s*d|doctoral)\b", re.I)

MASTERS = re.compile(
    r"\b(m\s*\.?\s*tech|mtech|m\s*\.?\s*e\b|m\s*\.?\s*s\s*\.?\s*c|msc"
    r"|m\s*\.?\s*c\s*\.?\s*a|mca|m\s*\.?\s*s\b|\bms\b|m\s*\.?\s*phil|mphil"
    r"|m\s*\.?\s*a\b|m\s*\.?\s*b\s*\.?\s*a|mba|m\s*\.?\s*com"
    r"|master(?:s|'s)?(?: of| in)?|post\s?graduate|pg diploma)\b", re.I)

BACHELORS = re.compile(
    r"\b(b\s*\.?\s*tech|btech|b\s*\.?\s*e\b|b\s*\.?\s*s\s*\.?\s*c|bsc"
    r"|b\s*\.?\s*c\s*\.?\s*a|bca|b\s*\.?\s*s\b|\bbs\b|b\s*\.?\s*a\b"
    r"|b\s*\.?\s*com|bachelor(?:s|'s)?(?: of| in)?|under\s?graduate)\b", re.I)

# A single programme that confers two levels. Detected BEFORE level, because
# "Integrated M.Tech" is both a bachelor's and a master's and the plain
# level test would see only the master's.
INTEGRATED = re.compile(
    r"\b(integrated|dual degree|dual[- ]degree|b\s*\.?\s*tech\s*[-+/]\s*m\s*\.?\s*tech"
    r"|bs\s*[-+/]\s*ms|b\s*\.?\s*s\s*\.?\s*[-+/]\s*m\s*\.?\s*s\s*\.?"
    r"|5[- ]year|five[- ]year)\b", re.I)


def level_of(text: str | None) -> str | None:
    """'doctorate' | 'masters' | 'bachelors' | None."""
    if not text:
        return None
    if DOCTORATE.search(text):
        return "doctorate"
    if MASTERS.search(text):
        return "masters"
    if BACHELORS.search(text):
        return "bachelors"
    return None


def is_integrated(text: str | None) -> bool:
    """True for an integrated or dual-degree programme."""
    return bool(text and INTEGRATED.search(text))


# ---------------------------------------------------------------------------
# 2. EQUIVALENCE - how close the qualification is to the one advertised
# ---------------------------------------------------------------------------
# Expressed as a fraction of the level's category marks.
#
# WHY NOT ALL-OR-NOTHING: scoring a B.Sc at zero because the advert says
# B.Tech screens out physics, mathematics and statistics graduates before
# their research record is read at all - and those are exactly the people
# a computing department hires for theory and ML. A ratio keeps them in
# the running while still pricing the difference in technical depth that
# the policy asks us to price.
#
# ORDER MATTERS. The first pattern that matches wins, so the more specific
# qualification is listed before the broader one it would otherwise fall
# into.

BACHELOR_EQUIVALENCE = (
    (re.compile(r"\b(b\s*\.?\s*tech|btech|b\s*\.?\s*e\b|bachelor of (?:technology|engineering)"
                r"|b\s*\.?\s*s\s*\.?\s*c\s*\.?\s*\(?\s*eng)\b", re.I),
     1.00, "B.Tech / B.E. or equivalent engineering bachelor's"),
    (re.compile(r"\b(b\s*\.?\s*c\s*\.?\s*a|bca|bachelor of computer applications)\b", re.I),
     0.85, "BCA - computing bachelor's, shorter technical core than B.Tech"),
    (re.compile(r"\b(b\s*\.?\s*s\s*\.?\s*c|bsc|\bbs\b|b\s*\.?\s*s\b"
                r"|bachelor of science)\b", re.I),
     0.75, "B.Sc / BS - science bachelor's"),
)
BACHELOR_OTHER = (0.50, "other bachelor's degree")

MASTER_EQUIVALENCE = (
    (re.compile(r"\b(m\s*\.?\s*tech|mtech|m\s*\.?\s*e\b"
                r"|master of (?:technology|engineering))\b", re.I),
     1.00, "M.Tech / M.E. or equivalent technical master's"),
    (re.compile(r"\b(m\s*\.?\s*s\b|\bms\b|master of science by research)\b", re.I),
     1.00, "MS - research master's, treated as M.Tech-equivalent"),
    (re.compile(r"\b(m\s*\.?\s*c\s*\.?\s*a|mca|master of computer applications)\b", re.I),
     0.85, "MCA - computing master's"),
    (re.compile(r"\b(m\s*\.?\s*s\s*\.?\s*c|msc|master of science)\b", re.I),
     0.80, "M.Sc - science master's"),
    (re.compile(r"\b(m\s*\.?\s*phil|mphil)\b", re.I),
     0.80, "M.Phil - research master's"),
)
MASTER_OTHER = (0.50, "other master's degree")


# Words the controlled vocabulary uses that are NOT degree names. The
# model intermittently returns the enum value ("masters") where the
# qualification name was asked for - a documented, reproducible defect
# (KNOWN_DEFECTS.md section 2) that survived an explicit prompt
# instruction against it. Treating the enum as a qualification would
# price a real M.Sc at the generic "other" rate, so it is detected here
# and the EVIDENCE SPAN is read instead.
ENUM_NOT_A_DEGREE = re.compile(
    r"^\s*(phd|phd_pursuing|mphil|masters|bachelors|other|null|none)\s*$",
    re.I)


def name_from_evidence(stated: str | None, evidence: str | None,
                       level: str) -> tuple[str | None, str | None]:
    """
    Recover a qualification name the extractor failed to copy.

    Returns (name, recovery_note). The name is taken from the verbatim
    CV span that already backs the field, so nothing is invented: if the
    span does not contain a qualification this module recognises, the
    answer stays None and the criterion is flagged rather than guessed.
    """
    if stated and not ENUM_NOT_A_DEGREE.match(stated):
        return stated, None
    if not evidence:
        return None, None

    table = (BACHELOR_EQUIVALENCE if level == "bachelors"
             else MASTER_EQUIVALENCE)
    for pattern, _, label in table:
        match = pattern.search(evidence)
        if match:
            return match.group(0), (
                f"the extractor returned the placeholder {stated!r} instead "
                f"of the qualification name, so it was read from the CV text "
                f"it quoted ({match.group(0)!r} in {evidence[:60]!r})")
    return None, None


def equivalence(text: str | None, level: str) -> tuple[float, str, bool]:
    """
    (ratio, label, needs_review) for a qualification at `level`.

    needs_review is True when the CV named no qualification we recognise,
    so the reviewer is told we fell back to the generic ratio rather than
    being shown a number with no provenance.
    """
    table, fallback = ((BACHELOR_EQUIVALENCE, BACHELOR_OTHER)
                       if level == "bachelors"
                       else (MASTER_EQUIVALENCE, MASTER_OTHER))
    if not text:
        return fallback[0], "qualification not named in the CV", True
    for pattern, ratio, label in table:
        if pattern.search(text):
            return ratio, label, False
    return fallback[0], fallback[1], True


# ---------------------------------------------------------------------------
# 3. BRANCH RELEVANCE - subject fit with the advertised department
# ---------------------------------------------------------------------------
# Applied ONCE, to the bachelor's and master's categories only (faculty
# decision, 2026-10-29). The PhD, postdoctoral, experience and research
# criteria do not read it, so the same relevance factor structurally
# cannot be charged twice.

SUBJECT_FAMILIES = {
    "cse": re.compile(
        r"\b(computer science|computer engineering|computer applications"
        r"|computing|software engineering|information technology"
        r"|artificial intelligence|machine learning|data science"
        r"|data analytics|cyber ?security|information science"
        r"|\bcse\b|\bcs\b|\bit\b|\bai\b|\bml\b)\b", re.I),
    # NOTE ON THE \w* SUFFIXES. A stem needs one: "mathematic" followed by
    # \b cannot match "Mathematics", because the 's' is a word character
    # and there is no boundary before it. Writing the stem as
    # "mathematic\w*" matches the singular, the plural and "mathematical"
    # alike. Short acronyms deliberately keep a hard \b on both sides -
    # "ai\w*" would swallow "air" and "aided".
    "ece": re.compile(
        r"\b(electronic\w*|communication engineering|electrical"
        r"|instrumentation|signal processing|vlsi|embedded"
        r"|ece|eee)\b", re.I),
    "maths": re.compile(
        r"\b(mathematic\w*|statistic\w*|operations research"
        r"|computational science\w*)\b", re.I),
    "physics": re.compile(r"\b(physic\w*|photonic\w*|optic\w*)\b", re.I),
    "mechanical": re.compile(
        r"\b(mechanical|aerospace|automobile|production)\b", re.I),
    "civil": re.compile(r"\b(civil|structural|geotechnical)\b", re.I),
    "chemical": re.compile(r"\b(chemical|chemistr\w*|polymer\w*)\b", re.I),
    "bio": re.compile(
        r"\b(biotechnolog\w*|bioinformatic\w*|biolog\w*|life science\w*)\b",
        re.I),
    "management": re.compile(
        r"\b(management|business administration|commerce|finance)\b", re.I),
    "humanities": re.compile(
        r"\b(english|histor\w*|sociolog\w*|philosoph\w*|literature"
        r"|education)\b", re.I),
}

# How each family relates to a department. The advertised post picks the
# column. "adjacent" means a graduate of that family routinely teaches and
# publishes in the department; "distant" means they can, but the CV has to
# show it rather than assert it.
RELEVANCE_TO_DEPARTMENT = {
    "cse": {
        "core": ("cse",),
        "adjacent": ("ece", "maths"),
        "distant": ("physics", "mechanical", "civil", "chemical", "bio",
                    "management", "humanities"),
    },
    "ece": {
        "core": ("ece",),
        "adjacent": ("cse", "physics", "maths"),
        "distant": ("mechanical", "civil", "chemical", "bio", "management",
                    "humanities"),
    },
}

# How a family is spelled in front of a reviewer. Acronyms stay upper
# case; ordinary subject names do not, because a criterion note that
# shouts "PHYSICS" reads as an error rather than as a field of study.
FAMILY_LABEL = {
    "cse": "Computer Science", "ece": "Electronics", "maths": "Mathematics",
    "physics": "Physics", "mechanical": "Mechanical Engineering",
    "civil": "Civil Engineering", "chemical": "Chemistry",
    "bio": "Biosciences", "management": "Management",
    "humanities": "Humanities",
}

RELEVANCE_RATIO = {"core": 1.00, "adjacent": 0.80, "distant": 0.50}

DEFAULT_DEPARTMENT = "cse"


def subject_family(subject: str | None) -> str | None:
    """Which family a subject string belongs to, or None if unreadable."""
    if not subject:
        return None
    for family, pattern in SUBJECT_FAMILIES.items():
        if pattern.search(subject):
            return family
    return None


def branch_relevance(subject: str | None, department: str = DEFAULT_DEPARTMENT,
                     supporting_evidence: str | None = None) -> dict:
    """
    How relevant a degree's SUBJECT is to the advertised department.

    Returns {ratio, verdict, family, reason, needs_review, lifted}.

    THE LIFT RULE, which the policy explicitly asks for: a distant subject
    does not stay distant if the candidate's own research demonstrates the
    alignment. `supporting_evidence` (their research area and publication
    titles) is checked against the department's own vocabulary, and a hit
    raises 'distant' to 'adjacent'. It can never raise a subject to 'core'
    - documented work in a field is evidence of capability in it, not a
    qualification in it.

    UNREADABLE SUBJECT -> FULL RELEVANCE, AND SAID OUT LOUD. Defaulting
    downward would charge the candidate for our inability to parse their
    CV, which is the failure mode this project exists to avoid. The cost
    is that omitting a subject is mildly advantageous; the review flag is
    what keeps that visible instead of silent.
    """
    table = RELEVANCE_TO_DEPARTMENT.get(
        department, RELEVANCE_TO_DEPARTMENT[DEFAULT_DEPARTMENT])
    family = subject_family(subject)

    if family is None:
        return {
            "ratio": 1.00,
            "verdict": "unreadable",
            "family": None,
            "reason": ("the CV does not state this degree's subject clearly "
                       "enough to judge its fit with the department, so it is "
                       "counted at full relevance and flagged for you"),
            "needs_review": True,
            "lifted": False,
        }

    for verdict in ("core", "adjacent", "distant"):
        if family in table[verdict]:
            break
    else:
        verdict = "distant"

    lifted = False
    if verdict == "distant" and supporting_evidence:
        department_vocabulary = SUBJECT_FAMILIES.get(department)
        if department_vocabulary and department_vocabulary.search(supporting_evidence):
            verdict, lifted = "adjacent", True

    shown = FAMILY_LABEL.get(family, family)
    post = FAMILY_LABEL.get(department, department.upper())
    reasons = {
        "core": f"the subject is {shown}, the department's own field",
        "adjacent": (f"the subject is {shown}, a neighbouring field whose "
                     f"graduates routinely teach and publish in {post}"),
        "distant": (f"the subject is {shown}, which is not closely related "
                    f"to {post}"),
    }
    reason = reasons[verdict]
    if lifted:
        reason = (f"the subject is {shown}, but the candidate's own research "
                  f"is documented in {post}, so it is credited as a "
                  f"neighbouring field rather than a distant one")

    return {
        "ratio": RELEVANCE_RATIO[verdict],
        "verdict": verdict,
        "family": family,
        "reason": reason,
        "needs_review": False,
        "lifted": lifted,
    }


def visible_rubric() -> dict:
    """The degree rules, for an administrator to read."""
    return {
        "equivalence": {
            "bachelors": ([{"label": label, "ratio": ratio}
                           for _, ratio, label in BACHELOR_EQUIVALENCE]
                          + [{"label": BACHELOR_OTHER[1],
                              "ratio": BACHELOR_OTHER[0]}]),
            "masters": ([{"label": label, "ratio": ratio}
                         for _, ratio, label in MASTER_EQUIVALENCE]
                        + [{"label": MASTER_OTHER[1],
                            "ratio": MASTER_OTHER[0]}]),
        },
        "branch_relevance": {
            "ratios": RELEVANCE_RATIO,
            "departments": {
                name: {k: list(v) for k, v in table.items()}
                for name, table in RELEVANCE_TO_DEPARTMENT.items()
            },
            "default_department": DEFAULT_DEPARTMENT,
            "applied_to": ["bachelors", "masters"],
            "not_applied_to": ["doctorate", "postdoctoral",
                               "teaching_experience", "industry_experience",
                               "publications", "research_visibility"],
            "why_limited": (
                "Branch relevance is applied once, to the two categories "
                "that are genuinely about subject. Applying it again to the "
                "PhD or to research would charge the same fact twice."),
            "lift_rule": (
                "A distant subject is raised to adjacent when the "
                "candidate's documented research is in the department's "
                "field. It is never raised to core."),
        },
        "integrated_degrees": (
            "An integrated or dual-degree programme confers BOTH levels and "
            "is scored in both categories. Each category still takes only "
            "its best single qualification and is capped at its weight, so "
            "no level can be credited twice."),
        "disclaimer": ("DEMO/RESEARCH ratios for a student project. Not "
                       "approved recruitment policy."),
    }
