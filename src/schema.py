"""
STAGE 2 - The candidate schema (a specification, not executable logic).

This module is the single source of truth for "what we try to know about a
candidate". Nothing here validates, scores, or calls anything: Stage 3 fills
this shape, Stage 4 validates it, Stage 5 verifies publications against
OpenAlex, Stage 6 scores it. Keeping the spec inert means all four stages
argue from the same definition.

Two principles drive every decision below.

1. RULE 5 - EVERY FIELD CARRIES ITS EVIDENCE.
   No bare values. Each extracted field is a {"value", "evidence"} pair,
   where evidence is a VERBATIM span copied from the candidate's .txt.
   Stage 4 can then check that the span really occurs in the source, which
   is what makes a hallucination detectable at all.

2. RULE 3 - THE LLM NEVER COMPUTES A NUMBER.
   This has a sharp consequence for "teaching_years" and "industry_years".
   Those are arithmetic over dates, and arithmetic is exactly what the LLM
   must not do. So the schema does NOT ask the LLM for a year count. It asks
   for the raw position entries with their date ranges, and Python derives
   the durations in Stage 6. See POSITION_FIELDS and the note beneath it.
"""

from __future__ import annotations

# ===========================================================================
# 1. CONTROLLED VOCABULARIES
# ===========================================================================
# Free-text values are unscoreable - two CVs will phrase the same fact three
# ways. Every field that feeds a score maps onto a fixed vocabulary, and the
# LLM is instructed to choose from it or return null.

DEGREE_LEVELS = (
    "phd",          # doctorate AWARDED
    "phd_pursuing", # enrolled, not yet awarded - see the gate section
    "mphil",
    "masters",      # M.Tech / M.E. / M.Sc / MS / MCA
    "bachelors",    # B.Tech / B.E. / B.Sc
    "other",
)

POSITION_CATEGORIES = (
    "teaching",   # Assistant Professor, Lecturer, Teaching Assistant
    "research",   # PhD scholar, postdoc, project associate, JRF/SRF
    "industry",   # any non-academic employer
    "other",      # internships outside the above, admin roles, unclear
)
# Choosing the category is a LANGUAGE judgement ("is 'Project Associate' a
# research role?"), which is a legitimate LLM task. Converting the attached
# dates into a number of years is ARITHMETIC, which is Python's job.


# ===========================================================================
# 2. PUBLICATION STATUS - WHICH STATUSES EARN CREDIT, AND WHY
# ===========================================================================
# This is the field the whole publication criterion hinges on, so the
# reasoning is written down rather than buried in Stage 6.
#
# THE GOVERNING PRINCIPLE
#   Credit requires EVIDENCE that peer review concluded in the work's
#   favour. There are exactly two sources of such evidence, and they are
#   not equally strong:
#
#     (i)  SELF-ATTESTATION  - the candidate's own status claim on the CV.
#          Free to write, impossible for us to falsify.
#     (ii) INDEPENDENT CORROBORATION - an OpenAlex match found in Stage 5.
#          Costly to fake, checkable by a reviewer via a URL.
#
#   Neither alone is treated as decisive. Stage 6 combines them through
#   CREDIT_MATRIX below, so that every point awarded can be traced to which
#   kind of evidence supported it.
#
# NOTE ON AN EARLIER DRAFT OF THIS SPEC: it claimed status and verification
# were two INDEPENDENT axes - status deciding credit, verification deciding
# only confidence. That separation does not survive contact with the
# defaulting problem (see DEFAULT_STATUS_WHEN_UNTAGGED). Verification must be
# able to PROMOTE an unlabelled entry to credited, so it genuinely feeds
# credit. The matrix makes that coupling explicit and auditable instead of
# pretending it is not there.

PUBLICATION_STATUSES = (
    "published",
    "in_press",
    "accepted",
    "under_revision",
    "submitted",
    "to_be_communicated",
    "unknown",
)

# Every status collapses into one of three classes. Stage 6 reads the class,
# never the raw status, so adding a new surface form later cannot change
# scoring behaviour by accident.
STATUS_CLASS = {
    # COMPLETED - the candidate asserts peer review concluded favourably.
    # The scholarly contribution is finished; what remains is production
    # scheduling. Eligible for credit, subject to corroboration.
    "published":          "completed",
    "in_press":           "completed",
    "accepted":           "completed",

    # IN_PROGRESS - outcome genuinely unknown. Journals reject papers under
    # revision. Crediting these would score INTENT rather than ACHIEVEMENT,
    # and the claim is unfalsifiable: nobody outside the review process can
    # confirm or refute it, so a candidate could list twenty submitted papers
    # at zero cost. Never creditable, by any route.
    "under_revision":     "in_progress",
    "submitted":          "in_progress",
    "to_be_communicated": "in_progress",

    # UNKNOWN - no status stated and none inferable. Not an accusation and
    # not an achievement; an open question that Stage 5 may be able to close.
    "unknown":            "unknown",
}

# ---------------------------------------------------------------------------
# CREDIT_MATRIX - (status class, Stage 5 verification) -> fraction of credit
# ---------------------------------------------------------------------------
# This table IS the publication-credit policy. Stage 6 reads it and does
# nothing cleverer. Every cell is a deliberate position, justified below.
# INDETERMINATE is NOT zero. It means the pipeline could not reach an
# answer, and it must never be silently coerced to 0.0 - see the
# lookup_failed rationale below.
INDETERMINATE = None

CREDIT_MATRIX = {
    # Claimed complete AND independently corroborated, with the candidate's
    # name present in the record's author list. The strongest case available.
    ("completed",   "matched"):            1.00,
    # Same credit as "matched": journal + volume + first page + author list
    # identifies an article at least as reliably as a fuzzy title does.
    ("completed",   "matched_by_biblio"):  1.00,
    # Corroborated and author-confirmed, but the title or year was only
    # close. Probably the same paper; possibly a near-duplicate title.
    ("completed",   "weak_match"):         0.75,
    # LOWERED FROM 0.50. Claimed complete, nothing found. This is still NOT
    # treated as dishonesty - OpenAlex coverage is imperfect for regional,
    # very recent and some workshop venues. But 0.50 was indefensible on
    # arithmetic: at half credit, ten unverifiable claims exactly equal five
    # verified papers (10 x 0.50 == 5 x 1.00), making fabrication
    # break-even profitable per unit of effort. At 0.25 it takes twenty
    # fabricated entries to match five real ones - a quantity no reviewer
    # would skim past. The "claimed but could not be corroborated"
    # distinction is carried by the evidence note, not by the number.
    ("completed",   "not_found"):          0.25,
    # A strong title match EXISTS, but the candidate's name is absent from
    # its author list. The one genuinely adverse signal in the system: zero
    # credit AND a flag. Still proves nothing on its own - transliteration
    # variants and name changes are common - so the flag reads "could not
    # confirm authorship", never "not the author".
    ("completed",   "unconfirmed_author"): 0.00,

    # No status claimed, but an external index places the work in the
    # literature with this candidate among its authors. The match IS the
    # evidence; it needs no help from the CV. This is the promotion path
    # that makes defaulting to "unknown" safe.
    ("unknown",     "matched"):            1.00,
    ("unknown",     "matched_by_biblio"):  1.00,
    # Deliberately conservative and unchanged: an untagged entry with only
    # partial corroboration earns nothing, and is flagged for a human.
    ("unknown",     "weak_match"):         0.00,
    ("unknown",     "not_found"):          0.00,
    ("unknown",     "unconfirmed_author"): 0.00,

    # Declared incomplete. No amount of matching promotes these, because the
    # disqualifier is the state of PEER REVIEW, not our ability to find the
    # text. A preprint of an under-revision paper may well sit in OpenAlex;
    # that does not mean it passed review.
    ("in_progress", "matched"):            0.00,
    # Still zero. A preprint or an early-view record being findable says
    # nothing about whether peer review has concluded.
    ("in_progress", "matched_by_biblio"):  0.00,
    ("in_progress", "weak_match"):         0.00,
    ("in_progress", "not_found"):          0.00,
    ("in_progress", "unconfirmed_author"): 0.00,

    # LOOKUP FAILED - network error, timeout, HTTP 5xx, malformed response.
    # Scoring this as 0 would mean an OpenAlex outage silently zeroes every
    # candidate's publication record and the ranking still renders, looking
    # perfectly normal. That is the most dangerous failure mode in the whole
    # pipeline, because it is invisible. So: no number. The criterion
    # becomes indeterminate and the candidate routes to human review, the
    # same way an indeterminate eligibility gate does.
    ("completed",   "lookup_failed"):      INDETERMINATE,
    ("unknown",     "lookup_failed"):      INDETERMINATE,
    # Except here, where the answer was already 0 for reasons that have
    # nothing to do with the lookup.
    ("in_progress", "lookup_failed"):      0.00,
}

# ---------------------------------------------------------------------------
# CREDIT EXCLUSIONS - entries that earn ZERO regardless of the matrix
# ---------------------------------------------------------------------------
# The matrix above prices an entry once we accept it IS a publication claim.
# These rules decide whether it is eligible to be priced at all, and they
# run FIRST. An excluded entry is kept in the record with its flag visible -
# never deleted - but contributes 0.00 to the publication score.
#
# Without this, the "unknown + matched = 1.00" promotion path becomes a
# loophole: Candidate_01's M.Tech dissertation was extracted as a
# publication with status "unknown", and a dissertation deposited in an
# institutional repository is exactly the kind of record OpenAlex indexes.
# It would then have been promoted to FULL credit. A thesis is real work
# and is already counted by the degree itself; crediting it again as a
# publication would double-count the same effort.
CREDIT_EXCLUSIONS = (
    {
        "id": "thesis_or_dissertation",
        "rule": "Stage 4 flagged the entry as a thesis/dissertation.",
        "credit": 0.00,
        "overrides_matrix": True,
        "reviewer_note": (
            "Recorded as a thesis or dissertation rather than a publication, "
            "so it earns no publication credit. The degree itself is scored "
            "separately. Reviewer may override."
        ),
    },
    {
        "id": "failed_validation",
        "rule": (
            "Stage 4 returned verdict 'unverified' for the entry - its "
            "title could not be found in the CV, or its claimed authors "
            "appear nowhere near it."
        ),
        "credit": 0.00,
        "overrides_matrix": True,
        "reviewer_note": (
            "Could not be corroborated against the CV text itself, so it is "
            "not scored. This is a statement about traceability, not an "
            "accusation."
        ),
    },
)
# Stage 6 MUST apply these before consulting CREDIT_MATRIX, and MUST record
# which exclusion fired next to the zero, so the audit shows WHY a listed
# publication scored nothing.


# ===========================================================================
# 4b. PATENTS - a subcategory of research output, not a criterion of its own
# ===========================================================================
# WHY A SUBCATEGORY AND NOT A SIXTH CRITERION
#   A separate top-level criterion would give every candidate in a
#   patent-light discipline a guaranteed zero on a scored line. Mathematics,
#   history and most of theoretical physics do not file patents, and a
#   rubric that reads that as a deficiency is measuring the field, not the
#   person. Folding patents into research output means a candidate is
#   credited for the contributions they actually made, in whatever form
#   their discipline produces them.
#
# WHY PATENTS ARE DISCOUNTED AGAINST PEER-REVIEWED WORK
#   A granted patent has passed substantive examination for novelty by a
#   patent office - a real, independent check, and the reason granted
#   patents earn most of a publication's credit. But it is an examination
#   for patentability, not for scholarly contribution, and the two are not
#   interchangeable. A PUBLISHED APPLICATION has passed no such check at
#   all: publication at 18 months is automatic and says only that a filing
#   exists. A PENDING or FILED application is a receipt.
#
#   DEMO/RESEARCH values. These ratios are a proposal, not sector policy,
#   and need faculty approval before any real use.
PATENT_STATUSES = (
    "granted",       # examined and granted by a patent office
    "published",     # application published (automatic at ~18 months)
    "filed",         # application submitted, not yet published
    "unknown",       # the CV states no stage
)

# Credit in PUBLICATION-EQUIVALENTS, so patents and papers share one scale
# and one saturation point. A granted patent is worth 0.75 of a
# corroborated paper; an unexamined filing is worth nothing.
PATENT_CREDIT = {
    "granted": 0.75,
    "published": 0.25,
    "filed": 0.00,
    "unknown": 0.00,
}

# Hard ceiling on what patents may contribute to the research criterion,
# as a fraction of its weight.
#
# WHY A CEILING AT ALL: patents are cheap to list and hard to verify from a
# CV. Without a cap, a candidate listing twenty granted patents would
# saturate a criterion whose stated purpose is peer-reviewed scholarship,
# and would outscore a candidate with a strong publication record. The cap
# lets patents matter without letting them substitute for peer review.
PATENT_SHARE_OF_RESEARCH = 0.25

# Patents a reviewer must look at rather than trust.
PATENT_EXCLUSIONS = (
    {
        "id": "duplicate_patent",
        "rule": ("The same application or grant number, or the same title, "
                 "appears more than once in the CV - commonly because a "
                 "filing and its later grant are both listed."),
        "credit": 0.00,
        "reviewer_note": (
            "Counted once only. A filing and the grant that follows it are "
            "one invention, not two."),
    },
    {
        "id": "failed_validation",
        "rule": "Stage 4 could not find the patent's title in the CV text.",
        "credit": 0.00,
        "reviewer_note": (
            "Could not be corroborated against the CV text. A statement "
            "about traceability, not an accusation."),
    },
    {
        "id": "no_identifier",
        "rule": ("Neither an application number nor a grant number is "
                 "stated, so the record cannot be looked up by anyone."),
        "credit": 0.00,
        "reviewer_note": (
            "No application or grant number is given, so this claim cannot "
            "be checked against any patent register. It stays visible and "
            "earns nothing until a number is supplied. Reviewer may "
            "override."),
    },
)

# NOT VERIFIED AGAINST ANY REGISTER, and the UI must say so.
#   OpenAlex does not index patents. Espacenet, Google Patents and the
#   Indian IPO all publish searchable data, but each needs either an API
#   key, a licence, or scraping - and this project has a zero-budget, no-
#   scraping constraint. So a patent's credit rests on the CV plus a
#   checkable number, never on an external confirmation.
#
#   This is a MEANINGFUL weakness and is the reason granted patents are
#   capped well below the research criterion's weight. It must never be
#   presented to a reviewer as though it carried the same evidential
#   standing as an OpenAlex-confirmed publication.
PATENT_VERIFICATION = {
    "verified": "confirmed against a patent register",
    "unverified": "a number is stated but no register was consulted",
    "unavailable": "no register could be consulted for this record",
}


# WHY (c) RESOLVED AS "NO SPECIAL DISCOUNT FOR accepted / in_press".
# The question was whether accepted/in_press should score below published.
# They should not - at the STATUS level. Acceptance is the moment peer
# review concludes; publication is the printer's timetable. Docking a
# candidate for their journal's production queue measures the journal, not
# the candidate.
#
# The real worry behind the question is the gaming one: "[Accepted]" is as
# cheap to type as "[Under Revision]", and an accepted paper usually has no
# DOI yet, so it is unfalsifiable. That worry is valid - and it is already
# answered by the matrix, without a special case. An unverifiable accepted
# paper lands on ("completed", "not_found") = 0.50, and so does an
# unverifiable PUBLISHED claim. The discount attaches to ABSENCE OF
# CORROBORATION, which is the thing we actually lack, rather than to the
# word the candidate chose. One rule, applied evenly, and no incentive to
# relabel "accepted" as "published" to dodge a status-specific penalty.
#
# KNOWN LIMITATION, recorded rather than hidden: this ties credit to OpenAlex
# coverage, which is weaker for some venues, languages and regions. A real
# publication in an unindexed venue is capped at 0.50 through no fault of
# the candidate. Mitigations: the penalty is partial rather than zero, the
# reason is shown with its evidence URL, and a human can override it.

# CRITICAL DISTINCTION, for Stage 6 and for the UI:
#   "does not count"  !=  "counts against"  !=  "suspicious".
# Work under revision is normal, honest scholarly activity. It must be:
#   - stored in the profile,
#   - shown to faculty as context,
#   - excluded from the numeric score,
#   - and NEVER treated as a hallucination or a red flag.
# A candidate is not penalised for disclosing in-progress work; they simply
# are not yet credited for it. Stage 7's explanation must say exactly this.
#
# The same applies to "unknown", and matters more now that it is the default
# for untagged entries: "unknown" is an open question about OUR knowledge,
# never an allegation about the candidate. Wording in the UI and in Stage 7
# must be "we could not corroborate this entry", not "unverified claim".
NON_COUNTING_IS_NOT_A_PENALTY = True

# WHY STATUS IS WHAT MAKES VERIFICATION INTERPRETABLE AT ALL.
# Stage 5 returns matched / weak_match / not_found from OpenAlex. That result
# is MEANINGLESS without the declared status beside it:
#
#   status=published      + not_found -> mildly notable. Could be a low-index
#                                        venue, a very new paper, or an
#                                        inaccurate claim. Flag for human
#                                        attention; prove nothing.
#   status=under_revision + not_found -> EXPECTED. Carries zero information.
#                                        Flagging it would be a false alarm.
#   status=unknown        + not_found -> UNRESOLVED, not adverse. We simply
#                                        never learned what this entry is.
#                                        Report as "insufficient evidence".
#
# Without the status field, the pipeline cannot tell those three cases apart
# and would cry wolf on every honest in-progress paper. That - not tidiness -
# is why this field exists.

# DEFAULTING RULE.
# An untagged entry becomes "unknown". It is NEVER assumed published, no
# matter how complete the citation looks.
#
# An earlier draft defaulted to "published" when a venue and year were
# present, on the convention that CVs tag only the exceptions. That was
# wrong, and the objection is an incentive one: if silence reads as
# "published", then the candidate who writes nothing is credited while the
# candidate who honestly writes "[Under Revision]" is not. The system would
# be paying people to omit status tags - rewarding exactly the opacity it
# exists to penetrate. Candidate_02 labels four in-progress papers; that
# transparency must never cost them against someone who labelled nothing.
#
# Defaulting to "unknown" costs the honest nothing, because the promotion
# path restores them: a genuine publication is found by Stage 5 and lands on
# ("unknown", "matched") = full credit. The entry earns credit from the
# INDEX rather than from the candidate's silence. Candidates 03 and 04, who
# tag nothing but list real published work, are credited through exactly
# this route.
#
# Residual cost, stated plainly: a real publication in a venue OpenAlex does
# not index, listed without a status tag, scores 0 rather than 0.50. That is
# the price of closing the omission loophole, and it is visible in the audit
# output as "insufficient evidence" with the lookup URL, so a reviewer can
# correct it.
DEFAULT_STATUS_WHEN_UNTAGGED = "unknown"

# Surface forms seen in real CVs, mapped to the vocabulary. Candidate_02 uses
# bracketed tags ("[Under Revision]"); Candidate_04 writes it inline
# ("European Physical Letters, in press (2026)"). Both forms must be caught.
STATUS_SURFACE_FORMS = {
    "published": "published",
    "in press": "in_press",
    "accepted": "accepted",
    "under revision": "under_revision",
    "under review": "under_revision",
    "revision submitted": "under_revision",
    "submitted": "submitted",
    "communicated": "submitted",
    "under communication": "submitted",
    "to be communicated": "to_be_communicated",
    "in preparation": "to_be_communicated",
}


# ===========================================================================
# 3. THE EXTRACTED PROFILE - what Stage 3 asks the LLM to produce
# ===========================================================================
# Shape of every scalar field:
#     {"value": <typed value or null>, "evidence": "<verbatim source span>"}
# If the CV does not state something, value MUST be null and evidence "".
# Guessing is the failure mode this whole project exists to prevent.

IDENTITY_FIELDS = {
    # Identity is for the faculty reviewer, NOT for scoring. Real names are
    # retained deliberately: a recruitment system must show who is being
    # evaluated. These fields are never inputs to any score.
    "full_name":   "Candidate's name as printed on the CV.",
    "email":       "Primary email, or null.",
    "orcid":       (
        "ORCID iD if printed. The ONLY identifier that truly anchors a person "
        "to a publication record. None of the four sample CVs carry one, "
        "which is exactly why Stage 5 can offer assisted matching and not "
        "proof of authorship."
    ),
    "scholar_url": (
        "Google Scholar / ResearchGate profile URL if printed. Not an API we "
        "can query, but a link a human reviewer can open - so it is evidence "
        "for a person, not for the pipeline."
    ),
}

EDUCATION_FIELDS = {
    "highest_degree": (
        f"One of {DEGREE_LEVELS}. Must distinguish 'phd' (AWARDED) from "
        "'phd_pursuing' (enrolled). The distinction decides eligibility, so "
        "it is the single highest-stakes field in the schema."
    ),
    "phd_status": (
        "'awarded' | 'pursuing' | 'none'. Deliberately redundant with "
        "highest_degree, because the gate depends on it and a second, "
        "directly-asked signal lets Stage 4 catch disagreement between them."
    ),
    "phd_institution": "Institution awarding/hosting the PhD, else null.",
    "phd_start_year":  "Integer year PhD began, else null.",
    "phd_year": (
        "Integer year the PhD was AWARDED. Null while pursuing. An open-ended "
        "range ('2020 - Present', '2025 - Present') means null, NOT the "
        "current year - inferring completion would manufacture eligibility."
    ),
    "masters_degree":      "e.g. 'M.Tech Computer Science and Engineering'.",
    "masters_institution": "Institution, else null.",
    "masters_year":        "Integer completion year, else null.",
    "net_qualified": (
        "true/false/null. True only where the CV states the candidate "
        "qualified UGC NET, CSIR NET, or a State Eligibility Test "
        "(SET/SLET) - the national teaching-eligibility examinations. "
        "null when the CV is silent. "
        "DISTINCT FROM 'GATE Qualified': GATE is an engineering entrance "
        "examination and confers no teaching eligibility. The two appear "
        "side by side on CVs (Candidate_01 lists both), so conflating them "
        "would manufacture eligibility out of the wrong exam."
    ),
}

RESEARCH_FIELDS = {
    "research_area": (
        "Short phrase in the candidate's own words ('Intelligent Reflecting "
        "Surface aided NOMA', 'nonlinear dynamics'). Used for fit commentary "
        "and never converted into a score - judging topical fit against a "
        "department's needs is a human call, not an arithmetic one."
    ),
}

POSITION_FIELDS = {
    # One entry per role. Python derives teaching_years / industry_years from
    # these in Stage 6. See the rule-3 note at the top of this module.
    "title":        "Role title exactly as printed.",
    "organisation": "Employer or institution.",
    "category":     f"One of {POSITION_CATEGORIES}.",
    "start_date":   "'YYYY-MM' or 'YYYY' as printed; null if absent.",
    "end_date":     "'YYYY-MM' / 'YYYY', or null when ongoing.",
    "is_current":   "true when the CV says 'Present' / 'Till date'.",
    "evidence":     "Verbatim line(s) the entry was read from.",
}
# WHY NOT A teaching_years NUMBER FROM THE LLM:
#   (a) Rule 3 forbids the LLM computing anything.
#   (b) Date arithmetic is where small models reliably fail.
#   (c) Overlapping roles need a documented policy (we will MERGE overlapping
#       intervals rather than sum them, so a concurrent TA-ship and lecturer
#       post cannot inflate a total past wall-clock time). That policy belongs
#       in reviewable Python, not in a prompt.
#   (d) "Present" must resolve against a RECORDED run date, or the same CV
#       silently scores differently next year. Stage 6 stamps that date.

PUBLICATION_FIELDS = {
    "title":   "Paper title, verbatim, without surrounding quotes.",
    "authors": (
        "Author list in printed order. Needed because matching the "
        "candidate's name inside an OpenAlex author list is the strongest "
        "authorship signal available absent an ORCID - and because author "
        "POSITION (first vs middle) is a real quality signal Stage 6 can use."
    ),
    "year":    "Integer publication year, else null.",
    "venue":   "Journal or conference name as printed.",
    "doi":     (
        "DOI if printed, else null. Worth keeping even though NONE of the "
        "four sample CVs print one: when present it collapses Stage 5 from "
        "fuzzy title matching to an exact lookup."
    ),
    "status":  (
        f"One of {PUBLICATION_STATUSES}. Extract only what the CV actually "
        "states, in brackets ('[Accepted]') or inline ('in press (2026)'). "
        "If no status is stated, return 'unknown' - do NOT infer 'published' "
        "from a complete-looking citation. See section 2."
    ),
    "evidence": "The verbatim citation line, which backs every field above.",
}

PROFILE_SKELETON = {
    "full_name":   {"value": None, "evidence": ""},
    "email":       {"value": None, "evidence": ""},
    "orcid":       {"value": None, "evidence": ""},
    "scholar_url": {"value": None, "evidence": ""},

    "highest_degree":  {"value": None, "evidence": ""},
    "phd_status":      {"value": None, "evidence": ""},
    "phd_institution": {"value": None, "evidence": ""},
    "phd_start_year":  {"value": None, "evidence": ""},
    "phd_year":        {"value": None, "evidence": ""},
    "masters_degree":      {"value": None, "evidence": ""},
    "masters_institution": {"value": None, "evidence": ""},
    "masters_year":        {"value": None, "evidence": ""},
    "net_qualified":       {"value": None, "evidence": ""},

    "research_area": {"value": None, "evidence": ""},

    "positions":    [],  # objects shaped by POSITION_FIELDS
    "publications": [],  # objects shaped by PUBLICATION_FIELDS
}


# ===========================================================================
# 4. ELIGIBILITY GATE - separate from scoring, and necessarily so
# ===========================================================================
# DEMO/RESEARCH RUBRIC. These are illustrative criteria for a student
# project. They are NOT LNMIIT's hiring criteria and carry no authority.
#
# WHY A GATE CANNOT JUST BE A HEAVILY-WEIGHTED CRITERION
#
# 1. A weighted score lets a disqualifier be out-voted. If "holds a PhD" were
#    worth 25 of 100 points, a strong candidate without the degree could
#    out-rank a weaker one who has it, and the ranking would then recommend
#    someone who cannot be appointed. The sample set shows this is not
#    hypothetical: Candidate_02 has 13 publications, a DST-INSPIRE
#    fellowship and an M.Tech gold medal, but has been a PhD *scholar* since
#    Nov 2020 with no award year. On publication strength alone that
#    candidate tops the list while failing the one mandatory requirement.
#
# 2. The two outputs mean different things. "61/100" invites comparison;
#    "minimum qualification not met" demands a different decision (reject,
#    or consciously waive) and a different UI treatment. Collapsing the
#    second into the first destroys information the reviewer needs.
#
# 3. A gate is contestable; diluted arithmetic is not. A reviewer can see
#    "failed: PhD awarded" with its evidence span and override that one rule.
#    Nobody can meaningfully override "-25 points".
#
# 4. Eligibility is boolean by nature. 70% of a mandatory requirement is not
#    70% of eligible.
#
# HOW IT BEHAVES
#   - Gates are evaluated BEFORE scoring and are completely independent of
#     it: a gate never adds, subtracts or scales any score.
#   - Every candidate is still fully scored, including the ineligible ones.
#     Faculty see the whole picture, the reviewer may know of an exception,
#     and suppressing the score would hide evidence rather than present it.
#   - The ranked list presents eligible candidates and gated candidates in
#     SEPARATE groups. Ineligible candidates are never silently dropped and
#     never silently interleaved.
#   - A human can override any gate, with a reason, logged (Stage 8).

GATE_OUTCOMES = ("met", "not_met", "indeterminate")

ELIGIBILITY_GATES = (
    {
        "id": "doctorate_or_net",
        "requirement": (
            "Doctorate awarded, OR qualification in a national teaching-"
            "eligibility examination (UGC NET / CSIR NET / SET / SLET)."
        ),
        "mandatory": True,
        "reads": ("phd_status", "highest_degree", "phd_year", "net_qualified"),
        "rule": (
            "met           when phd_status == 'awarded' AND phd_year is not null, "
            "              OR net_qualified is true; "
            "indeterminate when a doctorate is EVIDENCED but its award is not "
            "              confirmed - either no award year is stated, or "
            "              Stage 4's cross-check withheld the claim - and "
            "              net_qualified is not true; "
            "not_met       when there is no doctorate and no NET/SET pass."
        ),
        # "Evidenced but unconfirmed" must land on indeterminate, NOT
        # not_met. When Stage 4 withholds an 'awarded' claim it is saying
        # "we could not confirm this", which is a different statement from
        # "this person has no doctorate". Treating a withheld claim as
        # not_met would punish the candidate for OUR uncertainty.
        "doctorate_evidenced_by": (
            "highest_degree in {phd, phd_pursuing}, or phd_institution "
            "present, or Stage 4 recorded doctorate_cross_check_failed"
        ),
        # WHY THIS IS A DISJUNCTION, not "PhD only".
        # The original gate read "PhD awarded", which made Candidate_01
        # not_met. But his CV lists "UGC NET Qualified (Dec 2024)" and he
        # currently HOLDS an Assistant Professor post on an M.Tech. A gate
        # that calls a working Assistant Professor categorically ineligible
        # is modelling the wrong rule: NET is the recognised alternative
        # route to teaching eligibility in India, which is the entire reason
        # the examination exists. Requiring the doctorate alone would encode
        # a stricter policy than the sector actually applies.
        #
        # NOTE ON A NAME COLLISION: "GATE Qualified" on a CV refers to the
        # Graduate Aptitude Test in Engineering - an entrance examination,
        # unrelated to teaching eligibility and unrelated to this
        # eligibility "gate". Candidate_01 lists BOTH UGC NET and GATE.
        # Only NET/SET may satisfy this rule; see the net_qualified spec.
        "on_indeterminate": "route to human review; never auto-reject",
    },
    {
        "id": "any_teaching_or_research_experience",
        "requirement": "At least one teaching or research position evidenced.",
        "mandatory": False,   # advisory: informs, does not exclude
        "reads": ("positions",),
        "rule": "met when >=1 position has category in {teaching, research}.",
    },
)

GATE_IS_INDEPENDENT_OF_SCORE = True  # Stage 6 must not read gate outcomes.


# ---------------------------------------------------------------------------
# REVIEWER FLAGS - observations that inform a human and NEVER move a number
# ---------------------------------------------------------------------------
# A third category, distinct from both gates and scores. A flag is something
# a reviewer should look at and the pipeline has no business judging.
#
# The rule is absolute: a flag must never add, subtract or scale any score,
# and must never set a gate outcome. If the pipeline cannot evaluate
# something fairly, it says so and hands it over - it does not quietly price
# it in. Scoring an ambiguity would mean guessing at an explanation, which
# is exactly what this project refuses to do.
REVIEWER_FLAGS = (
    {
        "id": "concurrent_employment_and_study",
        "trigger": (
            "A position's date range overlaps a full-time degree's date "
            "range, particularly when the employer and institution are in "
            "different cities or states."
        ),
        "affects_score": False,
        "affects_gate": False,
        "wording": (
            "Employment dates overlap a degree enrolment - please check. "
            "This is often entirely legitimate (distance or part-time "
            "programmes, study leave, industry-sponsored study, or a CV "
            "that simply rounds its dates). Shown for the reviewer's "
            "attention only; it affects no score and no eligibility "
            "outcome."
        ),
        # Neutral by construction. The flag says "please check", never
        # "discrepancy" or "inconsistency" - the pipeline has not detected
        # wrongdoing, only an overlap it cannot interpret.
        #
        # MUST NOT FIRE for teaching or research assistantships held during
        # the same degree. A TA-ship, JRF/SRF, project assistantship or
        # research scholarship concurrent with one's own enrolment is the
        # normal structure of doctoral study, not an anomaly. Flagging it
        # would bury the reviewer in false alarms and, worse, would make
        # ordinary PhD candidates look suspect.
        "do_not_fire_when": (
            "the overlapping position is a teaching/research assistantship, "
            "research or summer internship, JRF/SRF, project associate, "
            "project fellow or research scholar role - judged from the role "
            "title OR the CV section it was listed under"
        ),
        # The section matters as much as the title. Candidate_04's
        # assistantship entries are named after the courses taught
        # ("PH-204: Physics Lab IV") and match no role keyword at all; only
        # the "Teaching Assistantship" heading they sit under identifies
        # them. Checking titles alone flagged a PhD student for doing the
        # teaching that every PhD student does.
        #
        # An earlier draft of this clause also required the employer to be
        # the same institution as the degree. That was dropped: comparing
        # institution names ("CURaj" vs "Central University of Rajasthan")
        # is itself a fuzzy-matching problem, and getting it wrong produces
        # the exact false alarm the rule exists to prevent. Over-flagging is
        # the harm here, so the looser, more reliable test is preferred.
        # Instance that prompted this, recorded so the rule is not abstract:
        # Candidate_03 holds Assistant Professor posts in Greater Noida (from
        # 2025-07) and Mumbai (2024-07 to 2025-06) while enrolled full-time at
        # CIT Kokrajhar, Assam - M.Tech 2023-2025, PhD 2025-present.
    },
)


# ===========================================================================
# 5. VERIFICATION SPEC - the contract Stage 5 must implement
# ===========================================================================
# Defined here, before Stage 3, because with zero DOIs across every sample
# CV the verification verdict now drives most of the publication score. The
# rule must be written down before any code can be tuned to flatter it.
#
# CONFIRMED: MATCHING IS NEVER TITLE-ONLY.
# A title match alone can NEVER produce "matched". The candidate's name must
# also be found in the OpenAlex record's author list. Without that check the
# system would credit a candidate for any famous paper whose title they
# typed, which is precisely the failure the project brief warns about.

VERIFICATION_OUTCOMES = (
    "matched",
    "matched_by_biblio",
    "weak_match",
    "unconfirmed_author",
    "not_found",
    "lookup_failed",
)

# WHY "matched_by_biblio" EXISTS - a title search is not a existence test.
# Candidate_04 lists "Principle of least action exhibited by active
# particles", Phys. Chem. Chem. Phys. 26, 25590 (2024). No title search
# finds it, because the paper was PUBLISHED under a different title:
# "Surface tension gradient invoked path selection", same journal, same
# volume 26, same page 25590, same five authors, DOI 10.1039/D4CP01832J.
# Titles changing between submission and publication is ordinary.
#
# Reporting that as "not_found" conflated two different statements - "our
# title query found nothing" and "this publication does not exist". Only
# the first was ever supported by the evidence. So when a title search
# comes back empty we retry on the bibliographic coordinates instead:
# author + year + venue + volume + first page.
#
# That tuple is a STRONGER identifier than a fuzzy title. Journal, volume
# and first page are very nearly a primary key for an article, and the
# author list corroborates it independently. So this outcome carries the
# same credit as "matched" - but it is recorded under its own name,
# because the reviewer should see that the CV title and the published
# title differ, and be able to judge that for themselves.

# --- Step 1: title similarity ---------------------------------------------
# Both strings are normalised first: lowercased, accents stripped, "&"->"and",
# punctuation and quotes removed, whitespace collapsed. Similarity is
# difflib.SequenceMatcher.ratio() over the normalised strings - standard
# library, so no new dependency, and deterministic.
TITLE_SIMILARITY_EXACT = 0.92   # >= this: titles are the same work
TITLE_SIMILARITY_CLOSE = 0.80   # >= this: plausibly the same work
#  < TITLE_SIMILARITY_CLOSE: not a candidate record at all.
#
# Why 0.92 rather than 1.0: real CVs differ from the indexed record by a
# subtitle, a hyphen, British vs American spelling, or an expanded acronym.
# Demanding exactness would report "not_found" for papers that plainly match.
# Why 0.80 as the floor: below it, short titles in a common subfield start
# colliding by chance ("Deep Learning for Image Classification").
#
# MEASURED on real titles from the sample CVs (normalised, SequenceMatcher):
#   same paper, case/punctuation/ampersand differences ....... 1.000  EXACT
#   same paper, indexed with trailing subtitle ............... 1.000  EXACT
#   DIFFERENT paper, same subfield:
#     "Taming explosive growth through linear augmentation" vs
#     "Taming explosive synchronization through linear augmentation"
#                                                               0.865  close
#   DIFFERENT papers, generic titles:
#     "Deep Learning for Image Classification" vs
#     "Deep Learning for Image Segmentation" ................. 0.811  close
#   unrelated titles ........................................ 0.388  none
#
# The two genuine near-collisions land in "close" and never reach EXACT, so
# neither can ever produce "matched" on title evidence alone. They can reach
# at most "weak_match", and only if the author check ALSO passes. This is
# the empirical case for the author cross-check being load-bearing rather
# than decorative: the 0.80 floor knowingly admits same-field collisions,
# and authorship is what stops them converting into credit.

# --- Step 2: year agreement -----------------------------------------------
YEAR_TOLERANCE = 1   # |cv_year - openalex_year| <= 1 counts as agreement.
# One year of slack is required, not generous: online-first publication and
# the assigned issue year routinely differ by one. A larger gap means a
# different edition or a different paper, and downgrades the verdict.

# --- Step 3: author cross-check (the decisive one) ------------------------
# Compare the candidate's name against every entry in the record's
# authorships list (both author.display_name and raw_author_name).
# Normalisation: lowercase, strip accents and punctuation, drop honorifics
# ("dr", "prof"), and handle "Yadav, Arpit Singh" as well as
# "Arpit Singh Yadav".
#
# An author is CONFIRMED when the surname matches exactly AND the first
# given name either matches in full or agrees on its initial (covering
# "A. S. Yadav" vs "Arpit Singh Yadav").
AUTHOR_MATCH_REQUIRES_SURNAME = True
AUTHOR_MATCH_ALLOWS_INITIAL   = True

# --- Step 4: combine into a verdict ---------------------------------------
VERDICT_RULES = (
    # (condition, outcome)
    ("title >= EXACT and author confirmed and year agrees", "matched"),
    ("title search empty, but author + year + venue + volume + first page "
     "all agree", "matched_by_biblio"),
    ("title >= EXACT and author confirmed and year differs", "weak_match"),
    ("title >= CLOSE and author confirmed",                  "weak_match"),
    ("title >= EXACT and author NOT confirmed",              "unconfirmed_author"),
    ("title >= CLOSE and author NOT confirmed",              "not_found"),
    ("no record above CLOSE",                                "not_found"),
    ("network error / timeout / 5xx / bad payload",          "lookup_failed"),
)
# Note the fourth rule: a strong title hit whose author list omits the
# candidate is NOT silently downgraded to "not_found". It is surfaced,
# because "this exact paper exists but we could not place you among its
# authors" is information a reviewer should see. The fifth rule is different
# - a merely CLOSE title with no author match is most likely a coincidental
# collision, and flagging it would generate noise.

# WHY AN AUTHOR-NAME MATCH IS STILL NOT PROOF OF AUTHORSHIP.
# This is the project's central honesty constraint and must appear verbatim
# in the UI and in Stage 7's explanations:
#
#   Matching a name against an author list raises CONFIDENCE. It does not
#   establish IDENTITY. Personal names are not unique identifiers. Only an
#   ORCID iD binds a person to a publication record, and none of the sample
#   CVs carry one.
#
# The weakness is not uniform, which makes it a fairness issue rather than
# just a technical one: common names collide far more often. Two of the four
# sample candidates are affected ("Ayush Kumar", "Arpit Singh Yadav"), so a
# homonym in the same broad field is entirely plausible for them and much
# less so for a rare name. The system must therefore never present
# "matched" as "verified authorship". The wording is "consistent with this
# candidate", and the OpenAlex URL ships alongside so a human can judge.
VERIFICATION_IS_ASSISTED_NOT_PROOF = True

# Evidence recorded for EVERY publication, matched or not (rule 5):
VERIFICATION_EVIDENCE_FIELDS = (
    "query_url",          # the exact OpenAlex request issued
    "openalex_id",        # matched work's ID, or null
    "matched_title",      # title as indexed, for side-by-side comparison
    "title_similarity",   # the computed ratio, so a reviewer can second-guess
    "year_cv",
    "year_openalex",
    "authors_openalex",   # the author list we checked against
    "author_match",       # which author entry matched, or null
    "outcome",            # one of VERIFICATION_OUTCOMES
    "checked_at",         # timestamp; OpenAlex is a moving target
)


# ===========================================================================
# 6. DELIBERATELY EXCLUDED - and why
# ===========================================================================
# Rule 7 and the brief both ask for a MINIMAL schema. Each exclusion below is
# a field we could extract but could not defensibly score.

EXCLUDED_FIELDS = {
    "cgpa_or_marks": (
        "Present in all four CVs (6.63, 8.91, 9.25, 9.21...) and tempting. "
        "Excluded: incomparable across institutions and scales (10-point vs "
        "4-point vs percentage), inflated differently by different awarding "
        "bodies, and near-irrelevant at faculty level where the doctorate "
        "and research record are the signal."
    ),
    "personal_information": (
        "Candidate_02 prints a 'Personal Information' block (DOB, marital "
        "status, nationality and similar). EXCLUDED ON PRINCIPLE: these are "
        "protected characteristics. Extracting them would import "
        "discrimination risk into a hiring tool for zero evaluative value, "
        "and they are the most sensitive category under the DPDP Act."
    ),
    "self_reported_citations_or_h_index": (
        "Never trust a CV's own metrics. Stage 5 takes citation counts from "
        "OpenAlex, which is checkable."
    ),
    "awards_and_honours": (
        "Real signal, but incommensurable: 'Gold Medalist' vs 'Best Paper' "
        "vs a merit scholarship cannot be placed on one scale without "
        "inventing a hierarchy we cannot justify. Shown to faculty as "
        "context via the raw text; not a scored field."
    ),
    "technical_skills": (
        "Noisy keyword lists ('VS Code', 'Bootstrap'). Weak evidence of "
        "scholarly or teaching capability."
    ),
    "fdps_workshops_online_courses": (
        "Candidate_03 lists several. Attendance is participation, not "
        "contribution, and certificate counts are trivially inflated."
    ),
    "projects": (
        "Overlaps publications and student coursework inconsistently. Would "
        "double-count research output for some candidates and not others."
    ),
    "referees": (
        "Third-party personal data with no scoring role. Collecting it would "
        "expand the system's PII footprint for nothing."
    ),
}
