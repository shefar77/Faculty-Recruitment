"""
STAGE 3 - CV text  ->  structured JSON, via a LOCAL model through Ollama.

This is the ONLY place in the pipeline where the LLM reads free text, and one
of only two places it is used at all (the other is Stage 7, writing prose
about numbers Python already computed). It never scores, ranks or decides.

WHAT format:"json" ACTUALLY GUARANTEES
  Ollama constrains token sampling so the output must parse as JSON. That is
  a guarantee about SYNTAX and nothing else. It does NOT promise:
    - that the keys we asked for are present, or that no extra keys appear
    - that values have the right type (a year may arrive as "2021" or null)
    - that enum fields hold a permitted value
    - that any of it is TRUE
  A fluent, well-formed JSON object full of invented facts satisfies
  format:"json" perfectly. That is why Stage 4 exists.

TEMPERATURE 0
  Greedy decoding: always take the highest-probability next token. Combined
  with a fixed seed this makes a run reproducible on this machine with this
  model build. It is NOT a correctness setting - a confidently wrong model is
  wrong identically every time. Reproducibility is for auditability.

CONTEXT WINDOW - the dangerous one
  qwen2.5:7b supports 32768 tokens, but Ollama defaults to num_ctx=2048 and
  SILENTLY DROPS THE OLDEST TOKENS past that. Measured on this machine: a
  prompt with a fact at the top returned a confabulated answer under the
  default, and the correct one at num_ctx=8192. Truncation eats the TOP of a
  CV - name, degree, institution - so the failure is both invisible and
  aimed at the fields that decide eligibility. We therefore set num_ctx
  explicitly and verify after every call that the prompt was not truncated.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"

# Large enough for the biggest sample CV (~4k tokens) plus instructions and
# the generated object, with headroom. Raise for longer CVs; the guard below
# will tell you when it is too small rather than letting it fail quietly.
NUM_CTX = 16384

SEED = 42
TEMPERATURE = 0
# (connect, read), not a single number.
#
# A scalar timeout applies to BOTH phases, so a host that accepts packets
# but never answers - firewalled, or a machine that has gone away - would
# stall the full read budget just trying to connect. At 900s across three
# extraction calls and five explanation calls that is over an hour before
# the API gives up on a server that was never going to reply.
#
# Connecting to a local Ollama either works at once or is not going to.
# Generating, on the other hand, is genuinely slow: Candidate_04's
# publication call takes ~580s on this machine, so the read budget has to
# stay generous or real work gets killed mid-flight.
# Read budget raised 900 -> 2400 after a real 11-page, 31k-character CV
# (Candidate_05) timed out mid-generation at 900s. Measured on this
# machine: its profile call alone took 435s, and the publications call -
# the longest, because it emits one JSON object per paper - was still
# running at 900s. A CV twice the size of anything in the sample set
# needs a budget to match, or the pipeline refuses work it could finish.
REQUEST_TIMEOUT = (10, 2400)

# If a prompt gets within this fraction of the window, assume truncation.
TRUNCATION_WARN_RATIO = 0.95

# How long Ollama keeps the model resident after a call. Its default is 5
# minutes, which is shorter than the gap between a reviewer uploading one
# CV and the next, so the 4.7GB model is evicted and reloaded between runs.
#
# Measured on this machine: load_duration was 10.7s on the first call of a
# run and 0.01s on every call after it. That 10.7s is pure overhead - the
# same model, read off disk again - and a reviewer working through a stack
# of CVs pays it once per CV.
#
# Not a correctness setting. It changes nothing about what the model
# returns; it only stops Ollama throwing away work it is about to need.
KEEP_ALIVE = "30m"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
# Two separate calls, not one. Reasons:
#   - Different failure modes. Scalars are a lookup task; publications are a
#     list task where the model tends to stop early or merge entries.
#   - A long publication list can crowd out the scalar fields in one object.
#   - When publications are weak we can re-run only that half.
# The project brief anticipated this split; the sample set makes it certain
# (Candidate_04 lists 19+ entries, Candidate_02 lists 13).

PROFILE_PROMPT = """You extract facts from an academic CV into JSON.

ABSOLUTE RULES
1. Copy ONLY what the CV states. Never infer, complete or guess.
2. If the CV does not state something, use null for "value" and "" for
   "evidence". A null is a correct answer. An invented value is a failure.
3. "evidence" must be text copied VERBATIM from the CV - the exact
   characters, not a paraphrase or summary. It is checked automatically.
4. Do not compute anything. Do not add up years or durations.

Return EXACTLY this JSON shape and no other keys:

{{
  "full_name":           {{"value": <string|null>, "evidence": <string>}},
  "email":               {{"value": <string|null>, "evidence": <string>}},
  "orcid":               {{"value": <string|null>, "evidence": <string>}},
  "scholar_url":         {{"value": <string|null>, "evidence": <string>}},
  "highest_degree":      {{"value": <"phd"|"phd_submitted"|"phd_pursuing"|"mphil"|"masters"|"bachelors"|"other"|null>, "evidence": <string>}},
  "phd_status":          {{"value": <"awarded"|"thesis_submitted"|"pursuing"|"none"|null>, "evidence": <string>}},
  "phd_institution":     {{"value": <string|null>, "evidence": <string>}},
  "phd_start_year":      {{"value": <integer|null>, "evidence": <string>}},
  "phd_year":            {{"value": <integer|null>, "evidence": <string>}},
  "phd_subject":         {{"value": <string|null>, "evidence": <string>}},
  "masters_degree":      {{"value": <string|null>, "evidence": <string>}},
  "masters_institution": {{"value": <string|null>, "evidence": <string>}},
  "masters_year":        {{"value": <integer|null>, "evidence": <string>}},
  "masters_subject":     {{"value": <string|null>, "evidence": <string>}},
  "bachelors_degree":      {{"value": <string|null>, "evidence": <string>}},
  "bachelors_institution": {{"value": <string|null>, "evidence": <string>}},
  "bachelors_year":        {{"value": <integer|null>, "evidence": <string>}},
  "bachelors_subject":     {{"value": <string|null>, "evidence": <string>}},
  "research_area":       {{"value": <string|null>, "evidence": <string>}}
}}

FIELD NOTES
- phd_status: "awarded" only if the CV shows the doctorate is COMPLETE.
  If the person is enrolled, a research scholar, or the PhD row has no end
  year (e.g. "2020 - Present"), use "pursuing" and leave phd_year null.
  Never treat an open-ended date range as a completion year.
  Use "thesis_submitted" ONLY when the CV says the thesis is submitted or
  under evaluation ("Ph.D. (Thesis Submitted)", "awaiting viva"). That is
  NOT "awarded": leave phd_year null, because a range like "2021 - 2026"
  is the enrolment period, not the year the degree was conferred.
- highest_degree: use "phd" only when phd_status is "awarded"; use
  "phd_pursuing" when they are enrolled but not finished.
- *_degree: the QUALIFICATION NAME exactly as printed - "B.Tech",
  "M.Tech", "MCA", "B.Sc", "Integrated M.Tech", "Dual Degree B.Tech-M.Tech".
  Do NOT put the subject here, and do NOT put a controlled-vocabulary word
  like "masters" or "bachelors" here. If the CV prints "Master of
  Technology", copy that.
- *_subject: the FIELD OF STUDY only, with no degree name attached -
  "Computer Science and Engineering", "Electronics and Communication",
  "Physics". If the CV prints "B.Tech in Civil Engineering", then
  bachelors_degree is "B.Tech" and bachelors_subject is "Civil
  Engineering". Null when the CV names no subject.
- *_institution: the awarding university or college, exactly as printed,
  with no campus address or department attached.
- bachelors_*: the FIRST degree - B.Tech, B.E., B.Sc, BCA, BS or similar.
  A CV that lists only a master's and a doctorate leaves these null.
- An INTEGRATED or DUAL degree ("Integrated M.Tech", "B.Tech-M.Tech Dual
  Degree") is ONE programme conferring two levels. Record it under the
  HIGHER level and copy the programme name verbatim so the word
  "Integrated" or "Dual" survives; leave the lower level null unless the
  CV lists a separate degree for it.
- Do NOT include positions or publications here. Separate requests handle
  those, each reading only the relevant section of the CV.

CV TEXT
---
{cv_text}
---
Return only the JSON object."""


PUBLICATIONS_PROMPT = """You extract the publication list from an academic CV into JSON.

ABSOLUTE RULES
1. Copy ONLY what the CV states. Never invent a paper, a venue or a year.
2. List EVERY publication entry you find, in the order printed. Do not stop
   early and do not summarise.
3. "evidence" must be the citation line copied VERBATIM from the CV.
4. If a field is not printed for an entry, use null.

Return EXACTLY this shape:

{{
  "publications": [
    {{"title": <string>,
      "authors": [<string>, ...],
      "year": <integer|null>,
      "venue": <string|null>,
      "doi": <string|null>,
      "status": <"published"|"in_press"|"accepted"|"under_revision"|"submitted"|"to_be_communicated"|"unknown">,
      "evidence": <string>}}
  ]
}}

STATUS - READ CAREFULLY
Report only the status the CV ACTUALLY STATES, as a bracketed tag
("[Accepted]", "[Under Revision]") or inline wording ("in press (2026)",
"communicated").
If the entry states NO status, use "unknown".
Do NOT infer "published" from a complete-looking citation with a venue and
year. A missing status is "unknown", always. This matters: assuming
"published" would reward omitting a status tag over honestly declaring one.

Map wording to values:
  "published"            -> published
  "in press"             -> in_press
  "accepted"             -> accepted
  "under revision"/"under review" -> under_revision
  "submitted"/"communicated"      -> submitted
  "to be communicated"/"in preparation" -> to_be_communicated
  nothing stated         -> unknown

Include entries of every kind: journal articles, conference papers, book
chapters. Exclude workshops ATTENDED, FDPs, courses and talks given - those
are not publications.

CV TEXT
---
{cv_text}
---
Return only the JSON object."""


# ---------------------------------------------------------------------------
# Ollama call
# ---------------------------------------------------------------------------

@dataclass
class CallResult:
    """One LLM round-trip, with the provenance Stage 7's audit log needs."""

    ok: bool
    data: dict = field(default_factory=dict)
    error: str = ""
    model: str = MODEL
    num_ctx: int = NUM_CTX
    seed: int = SEED
    temperature: float = TEMPERATURE
    prompt_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    truncated: bool = False
    called_at: str = ""
    # Ollama's own breakdown of where `seconds` went. Wall-clock alone
    # cannot tell a slow model from a reloaded one from a long prompt, and
    # that distinction is the whole of any performance question asked
    # later. Recording it here means the audit file answers it without
    # anyone having to re-run a profiler.
    #   load_seconds    - reading the model into memory (0 when resident)
    #   prefill_seconds - processing the input tokens
    #   decode_seconds  - generating the output tokens
    load_seconds: float = 0.0
    prefill_seconds: float = 0.0
    decode_seconds: float = 0.0
    # Fingerprint of the exact prompt text. Defect 4 in KNOWN_DEFECTS.md:
    # a one-line prompt edit silently moved accuracy from 92% to 82%, and
    # nothing in the output revealed that a different prompt had been used.
    # Recording the hash makes prompt drift visible in the audit trail and
    # lets a reviewer tell whether two records are even comparable.
    prompt_sha: str = ""


def call_ollama(prompt: str, *, num_ctx: int = NUM_CTX) -> CallResult:
    """
    One /api/generate call with format:"json".

    Returns a CallResult rather than raising, because a single bad call
    should degrade one field, not kill a batch run.
    """
    started = time.time()
    called_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",      # constrains SYNTAX only - see module docstring
        "keep_alive": KEEP_ALIVE,
        "options": {
            "temperature": TEMPERATURE,
            "seed": SEED,
            "num_ctx": num_ctx,
        },
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        return CallResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            seconds=round(time.time() - started, 1),
            called_at=called_at,
            prompt_sha=prompt_sha,
        )

    prompt_tokens = body.get("prompt_eval_count", 0) or 0
    output_tokens = body.get("eval_count", 0) or 0

    # The guard the needle test justified: if the prompt filled the window,
    # the front of the CV was dropped and anything "missing" is unreliable.
    truncated = prompt_tokens >= num_ctx * TRUNCATION_WARN_RATIO

    result = CallResult(
        ok=True,
        num_ctx=num_ctx,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        seconds=round(time.time() - started, 1),
        truncated=truncated,
        called_at=called_at,
        prompt_sha=prompt_sha,
        load_seconds=round((body.get("load_duration") or 0) / 1e9, 2),
        prefill_seconds=round((body.get("prompt_eval_duration") or 0) / 1e9, 2),
        decode_seconds=round((body.get("eval_duration") or 0) / 1e9, 2),
    )

    # format:"json" makes this parse almost always succeed. "Almost": the
    # model can still hit the token limit mid-object and produce valid-prefix
    # JSON that is truncated. So we check rather than assume.
    try:
        result.data = json.loads(body.get("response", ""))
    except json.JSONDecodeError as exc:
        result.ok = False
        result.error = f"model returned unparseable JSON: {exc}"

    if truncated:
        result.ok = False
        result.error = (
            f"prompt used {prompt_tokens} of num_ctx={num_ctx} tokens - the CV "
            f"was probably truncated from the top. Raise NUM_CTX and re-run."
        )

    return result


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_profile(cv_text: str) -> CallResult:
    """Scalar fields + positions. No publications."""
    return call_ollama(PROFILE_PROMPT.format(cv_text=cv_text))


# Cheap pre-check: if none of these appear anywhere in the CV, there is no
# NET/SET claim to extract and the LLM call is pure cost.
NET_TOKEN = re.compile(
    # Written without backslash escapes on purpose: an earlier version
    # used word boundaries and the escapes were mangled into literal
    # backspace characters, so the pattern silently matched nothing and
    # the pre-check would have skipped eligibility for the one candidate
    # who actually holds a NET qualification.
    "(^|[^a-z])(net|slet|set)([^a-z]|$)|eligibility test",
    re.I,
)


ELIGIBILITY_PROMPT = """You check one specific fact in an academic CV.

QUESTION: does the CV state that this person has QUALIFIED a national
teaching-eligibility examination in India?

Those examinations are exactly: UGC NET, CSIR NET, SET, SLET.

Return EXACTLY:

{{
  "net_qualified": {{"value": <true|false|null>, "evidence": <string>}}
}}

RULES
- true  : the CV says they qualified/cleared UGC NET, CSIR NET, SET or SLET.
- null  : the CV does not mention any of those. This is the normal answer.
- false : only if the CV explicitly says they did NOT qualify.
- "evidence" must be the exact phrase copied from the CV, or "" for null.

THESE DO NOT COUNT. If you see them, the answer is still null:
  GATE  - Graduate Aptitude Test in Engineering, an entrance examination
  JEE, JAM, GRE, GMAT, CAT - entrance examinations
  A fellowship or scholarship (JRF, SRF, INSPIRE) on its own

A CV may list GATE and UGC NET next to each other. Only the NET line counts.

CV TEXT
---
{cv_text}
---
Return only the JSON object."""


def extract_eligibility(cv_text: str) -> CallResult:
    """
    NET/SET qualification, as its own small call.

    MEASURED REASON FOR A THIRD CALL. This field was first added to the
    profile prompt. That single addition degraded unrelated fields badly:
    Candidate_03's ground-truth accuracy fell from 92% to 71% - its
    masters_degree, masters_institution and masters_year all went null and
    research_area drifted from the thesis topic to a generic department
    name. Candidate_02 saw 13 fields change. Same model, same seed, same
    temperature; the prompt was the only variable.

    The lesson generalises: a prompt is not a config file where options can
    be appended independently. Adding instructions competes for the model's
    attention with everything already there, and the cost lands on fields
    that have nothing to do with the addition. Isolating a field into its
    own call keeps that interference out of the fields that already work -
    the same reason publications were split out in the first place.
    """
    return call_ollama(ELIGIBILITY_PROMPT.format(cv_text=cv_text))


# --- Positions, extracted section by section -------------------------------
# Defect 1 in KNOWN_DEFECTS.md: a single whole-CV call took positions from
# the first experience-like heading and ignored later ones. Candidate_03 lost
# its entire "Internship" section - both of that candidate's industry roles -
# so industry_years computed to zero from a CV evidencing about nine months.
#
# Python finds the section boundaries; the model only reads the text inside
# each one. Same division of labour as the publication status guard.
#
# NOTE: PROFILE_PROMPT still asks for positions and is deliberately left
# untouched - editing a working prompt cost 10 points of accuracy once
# already (defect 4). The profile's positions are simply ignored in favour
# of these.

# The qualifier before the keyword varies a lot and can be several words:
# "Experience", "Work Experience", "Research & Professional Experience".
# Up to three leading words are allowed, "&" among them.
POSITION_SECTION_HEADING = re.compile(
    r"^[ \t]*(?:[A-Za-z&][A-Za-z&./-]*[ \t]+){0,3}"
    r"(experience|employment|internships?|appointments?|positions?|"
    r"assistantships?|work history|professional background)[ \t]*:?[ \t]*$",
    re.I | re.M,
)

# Headings that end a position section. Like the start pattern, these can
# carry a qualifier: Candidate_01's next heading is "Research Project", not
# "Projects", and without allowing the prefix the Experience section ran on
# and swallowed a dissertation project as if it were a job.
POSITION_SECTION_END = re.compile(
    r"^[ \t]*(?:[A-Za-z&][A-Za-z&./-]*[ \t]+){0,2}"
    r"(education|qualifications?|publications?|patents?|"
    r"skills?|awards?|honou?rs?|achievements?|projects?|"
    r"interests?|personal (information|details)|references?|"
    r"certifications?|courses?|programmes?|programs?|workshops?|"
    r"conferences?|memberships?|thesis|dissertation|declaration|"
    r"training|reviewer)"
    # The line must END here, give or take a few more heading-ish words.
    # Previously this was ".*$", which let ORDINARY PROSE pose as a
    # heading: Candidate_06's bullet "...aligned with outcome-based
    # education practices." matched on "education" and truncated the
    # Experience section mid-sentence, losing the Kalvium internship that
    # followed it. Requiring the line to end - and to carry no sentence
    # punctuation - is what distinguishes a heading from a sentence.
    r"(?:[ \t]+[A-Za-z&/()-]+){0,3}[ \t]*:?[ \t]*$",
    re.I | re.M,
)

POSITIONS_PROMPT = """You extract WORK POSITIONS from one section of an academic CV.

Return EXACTLY this shape:

{{
  "positions": [
    {{"title": <string>, "organisation": <string|null>,
      "category": <"teaching"|"research"|"industry"|"other">,
      "start_date": <string|null>, "end_date": <string|null>,
      "is_current": <true|false>, "evidence": <string>}}
  ]
}}

RULES
1. One object per role actually held. Include jobs, lectureships,
   professorships, assistantships, fellowships and internships.
2. organisation: the employer or institution.
   - It is often printed on the line ABOVE the role title, with the date
     range beside it. Look there before concluding there is none.
   - A city, state or country is a LOCATION, not an employer. Never put
     "Greater Noida, Uttar Pradesh" or "Remote" in this field.
   - Never put a course name, subject or department here: "Teaching
     Assistant - Computer Programming" has title "Teaching Assistant"
     and organisation null unless an employer is printed.
   - If no employer is printed anywhere nearby, use null. Never invent one.
3. category: "teaching" for lecturing/tutoring/professorships,
   "research" for PhD/postdoc/project research posts, "industry" for
   non-academic employers, "other" if unclear.
4. start_date / end_date: use "YYYY-MM" when a month is given, else
   "YYYY". If the role is ongoing ("Present", "Till date"), set end_date
   null and is_current true.
5. evidence: the verbatim line(s) the entry was read from.
6. Report ONLY roles in the text below. If it contains none, return an
   empty list.

SECTION TEXT
---
{section_text}
---
Return only the JSON object."""


def find_position_sections(cv_text: str) -> list[tuple[str, str]]:
    """Return [(heading, section_text)] for every experience-like heading."""
    sections = []
    for match in POSITION_SECTION_HEADING.finditer(cv_text):
        start = match.end()
        end_match = POSITION_SECTION_END.search(cv_text, start)
        next_heading = POSITION_SECTION_HEADING.search(cv_text, start)

        end = len(cv_text)
        for candidate_end in (end_match, next_heading):
            if candidate_end and candidate_end.start() < end:
                end = candidate_end.start()

        body = cv_text[start:end].strip()
        if body:
            sections.append((match.group(0).strip(), body))
    return sections


def extract_positions(cv_text: str) -> CallResult:
    """
    Positions from every experience-like section, merged.

    Falls back to the whole CV when no heading is recognised, so an
    unusual layout costs nothing rather than everything.
    """
    sections = find_position_sections(cv_text)
    if not sections:
        return call_ollama(POSITIONS_PROMPT.format(section_text=cv_text))

    merged: list[dict] = []
    combined = CallResult(ok=True, called_at=datetime.now(timezone.utc)
                          .isoformat(timespec="seconds"))

    for heading, body in sections:
        result = call_ollama(POSITIONS_PROMPT.format(section_text=body))
        combined.prompt_tokens += result.prompt_tokens
        combined.output_tokens += result.output_tokens
        combined.seconds = round(combined.seconds + result.seconds, 1)
        if not result.ok:
            combined.ok = False
            combined.error = (combined.error + " | " + result.error).strip(" |")
            continue
        found = result.data.get("positions", [])
        if isinstance(found, list):
            for entry in found:
                if isinstance(entry, dict):
                    entry["source_section"] = heading
                    merged.append(entry)

    combined.data = {"positions": merged}
    return combined


# Cheap pre-check, same reasoning as NET_TOKEN: if the word never appears,
# there is nothing to extract and the call is pure cost. Most CVs in most
# disciplines list no patents at all.
PATENT_TOKEN = re.compile("patent", re.I)


PATENTS_PROMPT = """You extract PATENTS from an academic CV into JSON.

ABSOLUTE RULES
1. Copy ONLY what the CV states. Never invent a patent, a number or a date.
2. List EVERY patent entry you find, in the order printed.
3. "evidence" must be the patent line copied VERBATIM from the CV.
4. If a field is not printed for an entry, use null. A null is correct.
5. Do not compute anything and do not count anything.

Return EXACTLY this shape:

{{
  "patents": [
    {{"title": <string>,
      "inventors": [<string>, ...],
      "number": <string|null>,
      "status": <"granted"|"published"|"filed"|"unknown">,
      "year": <integer|null>,
      "jurisdiction": <string|null>,
      "evidence": <string>}}
  ]
}}

STATUS - READ CAREFULLY
Report only the stage the CV ACTUALLY STATES:
  "granted" / "patent granted" / "issued"        -> granted
  "published" / "application published"          -> published
  "filed" / "application filed" / "pending"
    / "under examination" / "application no."    -> filed
  nothing stated about the stage                 -> unknown

These are DIFFERENT stages and must not be merged. A filed application has
passed no examination; a granted patent has. If the CV says only
"Patent (2023)" with no stage word, that is "unknown", not "granted".

NUMBER
Copy the application or grant number exactly as printed, including any
country prefix. If no number is printed, use null - do not construct one.

WHAT IS NOT A PATENT
Exclude copyrights, trademarks, design registrations, software licences
and published papers ABOUT a patented method. Only patents and patent
applications belong here.

CV TEXT
---
{cv_text}
---
Return only the JSON object."""


def extract_patents(cv_text: str) -> CallResult:
    """
    Patents, as their own call, and only when the CV mentions one.

    Gated on PATENT_TOKEN because most CVs list no patents: running this
    unconditionally would add a round trip - tens of seconds on CPU - to
    every candidate in order to extract an empty list. If the word is
    absent, an empty list is the only correct answer, which is exactly
    what is recorded.
    """
    if not PATENT_TOKEN.search(cv_text):
        return CallResult(
            ok=True, data={"patents": []}, seconds=0.0,
            called_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            error="skipped: CV contains no occurrence of 'patent'")
    return call_ollama(PATENTS_PROMPT.format(cv_text=cv_text))


def extract_publications(cv_text: str) -> CallResult:
    """
    Publication list only, as its own call.

    Measured on all four sample CVs, a single call over the whole CV text
    extracted every entry: 16/16 (Candidate_02), 19/19 (Candidate_04),
    1/1 (Candidate_03). An earlier draft added deterministic section-finding
    and chunking to fix "19 of 26 extracted" on Candidate_04 - but that
    count was wrong. Candidate_04's section holds TWO numbered lists: 19
    publications, then 26 organised conferences and poster presentations
    numbered from 1 again. The model had correctly taken all 19 and
    correctly excluded the events. The chunking was removed rather than
    kept "just in case": it added a section-boundary bug that swept those
    26 non-publications back in, to solve a problem that did not exist.
    """
    return call_ollama(PUBLICATIONS_PROMPT.format(cv_text=cv_text))


def extract_candidate(cv_text: str, stem: str) -> dict:
    """
    Run both calls and assemble one record.

    The record keeps the raw model output untouched alongside provenance for
    every call. Stage 4 validates it; nothing here judges correctness.
    """
    profile_call = extract_profile(cv_text)

    # Skip the eligibility call when the CV contains no NET/SET token at
    # all. The model cannot find a qualification that is not written down,
    # so this removes a whole round trip without removing any answer. A
    # false negative is impossible: if the words are absent, "null" is the
    # only correct result, which is exactly what we record.
    if NET_TOKEN.search(cv_text):
        eligibility_call = extract_eligibility(cv_text)
    else:
        eligibility_call = CallResult(
            ok=True, data={"net_qualified": {"value": None, "evidence": ""}},
            seconds=0.0, called_at=datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            error="skipped: CV contains no NET/SET/SLET token")

    # Positions come from their own section-scoped call, NOT from the
    # profile prompt. This extractor existed since Stage 6 but was only
    # reachable from refresh_positions.py - the pipeline itself still used
    # the profile call's positions, which read only the first
    # experience-like heading and silently dropped later ones.
    positions_call = extract_positions(cv_text)
    publications_call = extract_publications(cv_text)
    patents_call = extract_patents(cv_text)

    profile = dict(profile_call.data) if profile_call.ok else {}
    profile["positions"] = (positions_call.data.get("positions", [])
                            if positions_call.ok else [])
    # Merge the separately-extracted field in, so downstream stages still
    # see one flat profile object.
    if eligibility_call.ok:
        profile["net_qualified"] = eligibility_call.data.get(
            "net_qualified", {"value": None, "evidence": ""})
    publications = []
    if publications_call.ok:
        raw = publications_call.data.get("publications", [])
        if isinstance(raw, list):
            publications = raw

    patents = []
    if patents_call.ok:
        raw = patents_call.data.get("patents", [])
        if isinstance(raw, list):
            patents = raw

    return {
        "candidate_id": stem,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": profile,
        "publications": publications,
        "patents": patents,
        "provenance": {
            "profile_call": _provenance(profile_call),
            "eligibility_call": _provenance(eligibility_call),
            "positions_call": _provenance(positions_call),
            "publications_call": _provenance(publications_call),
            "patents_call": _provenance(patents_call),
        },
    }


def extraction_failures(record: dict) -> list[str]:
    """
    Which of the three extraction calls failed, if any.

    This exists because a failed extraction is INVISIBLE downstream. With
    Ollama unreachable, extract_candidate returns an empty but perfectly
    well-formed profile, validation passes it, scoring produces 0.0/100
    and the eligibility gate returns 'not_met' - a result identical to a
    genuinely unqualified applicant, with nothing anywhere saying the
    model was never reached.

    A pipeline that cannot run must say so, not quietly score someone
    zero. The caller is expected to abort, not to publish the record.
    """
    failed = []
    for name, provenance in (record.get("provenance") or {}).items():
        if not provenance.get("ok"):
            failed.append(f"{name}: {provenance.get('error') or 'unknown error'}")
    return failed


def _provenance(call: CallResult) -> dict:
    return {
        "ok": call.ok,
        "error": call.error,
        "model": call.model,
        "num_ctx": call.num_ctx,
        "seed": call.seed,
        "temperature": call.temperature,
        "prompt_tokens": call.prompt_tokens,
        "output_tokens": call.output_tokens,
        "seconds": call.seconds,
        "load_seconds": call.load_seconds,
        "prefill_seconds": call.prefill_seconds,
        "decode_seconds": call.decode_seconds,
        "truncated": call.truncated,
        "called_at": call.called_at,
        "prompt_sha": call.prompt_sha,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    project_root = Path(__file__).resolve().parent.parent
    out_dir = project_root / "output"

    if argv:
        texts = [Path(a) for a in argv]
    else:
        texts = sorted(out_dir.glob("Candidate_*.txt"))

    if not texts:
        print(f"No extracted .txt files in {out_dir}. Run extract_text.py first.")
        return 1

    print(f"Stage 3: field extraction  (model={MODEL}, temp={TEMPERATURE}, "
          f"seed={SEED}, num_ctx={NUM_CTX})\n")

    failures = 0
    for txt_path in texts:
        cv_text = txt_path.read_text(encoding="utf-8")
        print(f"{txt_path.stem} ... ", end="", flush=True)

        record = extract_candidate(cv_text, txt_path.stem)

        prof = record["provenance"]["profile_call"]
        pubs = record["provenance"]["publications_call"]
        n_pubs = len(record["publications"])

        out_path = out_dir / f"{txt_path.stem}.extracted.json"
        out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False),
                            encoding="utf-8")

        if prof["ok"] and pubs["ok"]:
            print(f"ok  | {n_pubs:>2} publications | "
                  f"{prof['prompt_tokens']}+{pubs['prompt_tokens']} prompt tokens | "
                  f"{prof['seconds'] + pubs['seconds']:.0f}s")
        else:
            failures += 1
            print("FAILED")
            for name, p in (("profile", prof), ("publications", pubs)):
                if not p["ok"]:
                    print(f"      {name}: {p['error']}")

    print(f"\nWrote *.extracted.json to {out_dir}")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
