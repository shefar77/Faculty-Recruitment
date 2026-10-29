"""
STAGE 7a - natural-language explanations of scores Python already computed.

This is the SECOND and last place the LLM is used (the first is Stage 3's
text -> JSON extraction). Here it does the one thing it is unambiguously
good at and that nothing else in the pipeline can do: turn a pile of
numbers and evidence into sentences a human can read.

WHAT THE MODEL IS AND IS NOT ALLOWED TO DO
  It receives the finished criterion - the score, the weight, the measured
  quantity, the evidence rows - and writes 3-4 sentences about it. It may
  not recompute, dispute, round, average or "correct" any number, and it
  may not introduce a fact that is not in the data it was handed.

  That is enforced in three ways, because a prompt alone has failed on
  every rule that mattered in this project (see KNOWN_DEFECTS.md):

    1. the prompt forbids it;
    2. the model is given ONLY the computed criterion, never the CV text,
       so it has no raw material to invent new facts from;
    3. every number it emits is checked against the numbers it was given,
       and an explanation that invents or alters one is REJECTED and
       replaced by a deterministic template.

  Point 3 is the one that actually holds. Prose that fails the check never
  reaches the audit file.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_fields as ef  # noqa: E402  (reuses the Ollama client)

MAX_SENTENCES = 6

# Mirrors scoring.ASSISTANTSHIP_CREDIT, imported lazily to keep this module
# free of a scoring dependency it otherwise does not need.
ASSISTANTSHIP_RATE = 0.5

EXPLANATION_PROMPT = """You explain one scoring criterion to a faculty
reviewer. A deterministic program has ALREADY computed the score. Your only
job is to put it into plain sentences.

Write 3 to 4 sentences. Return JSON exactly:
{{"explanation": "<your sentences>"}}

=== RULE 1: THE ONLY NUMBERS YOU MAY WRITE ===
You may write these numerals and NO OTHERS:

{allowed_numbers}

Before you finish, re-read your sentences and check every digit you typed
appears in that list. If a number is not in the list, DELETE that clause.
Never calculate a new number - no percentages you worked out yourself, no
counts you tallied, no differences, no averages, no totals.

  WRONG: "scored 10.0, which is 40% of the weight" (unless 40 is listed)
  WRONG: "across 7 roles" (unless 7 is listed)
  WRONG: "5 of the 19 papers" (unless both are listed)
  RIGHT: "scored 10.0 out of 25" (when 10 and 25 are listed)

=== RULE 2: DO NOT CLAIM FULL MARKS UNLESS points EQUALS weight ===
Compare the "points" and "weight" fields below. If points is less than
weight, you must NOT write "full marks", "maximum", "top marks" or "all
available points".

  WRONG (points 1.67, weight 10): "earning full marks for this criterion"
  RIGHT (points 1.67, weight 10): "earning 1.67 of the 10 available"

Note that a phrase like "full marks at 5.0 years" inside the data is a
THRESHOLD - it describes what would be needed, not what was achieved.

=== RULE 3: "NOT FOUND" DESCRIBES OUR SEARCH, NOT THE CANDIDATE ===
When a publication could not be matched, that means our index lookup found
nothing. It does NOT mean the paper is fictitious, and the candidate is
never to be described as dishonest. Papers are commonly missing from an
index because the venue is not indexed, the record is new, or the title
changed before publication.

  WRONG: "no evidence of its existence"
  WRONG: "the candidate never published this"
  WRONG: "this appears to be fabricated"
  RIGHT: "could not be corroborated against the index searched"
  RIGHT: "not independently verifiable from the source used"
  RIGHT: "the CV lists it, but the lookup returned no matching record"

=== RULE 4: DESCRIBE, DO NOT DECIDE ===
Never recommend hiring, rejecting, shortlisting or ranking. The faculty
reviewer decides. You only report what was measured.

=== RULE 5: NEUTRAL WORDING FOR MISSING EVIDENCE ===
If the criterion scored zero because the CV said nothing, write "the CV did
not state..." - never "the candidate failed to", "lacks", or "is weak in".

CRITERION DATA
{criterion_json}

Return only the JSON object."""


# Numbers that may legitimately appear in prose without being in the data:
# small counts the model may spell out, and the weight denominators.
def _numbers_in(text: str) -> set[str]:
    """Numeric tokens in a string, normalised so 10.0 and 10 compare equal."""
    found = set()
    for token in re.findall(r"\d+(?:\.\d+)?", str(text)):
        value = float(token)
        found.add(f"{value:g}")
    return found


def allowed_numbers(criterion: dict) -> set[str]:
    """Every number the model is permitted to state, drawn from its input."""
    allowed: set[str] = set()

    def absorb(value):
        if isinstance(value, dict):
            for item in value.values():
                absorb(item)
        elif isinstance(value, list):
            for item in value:
                absorb(item)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            allowed.add(f"{float(value):g}")
        elif isinstance(value, str):
            allowed.update(_numbers_in(value))

    absorb(criterion)

    # Rounding the model may reasonably do when reading a value aloud:
    # 10.85 -> "10.9", "10.8" or "11". Permitted because it restates a
    # given number rather than introducing a new one.
    #
    # Both directions are accepted at each precision. Python's round() is
    # not enough on its own: round(10.85, 1) returns 10.8, because 10.85
    # is not exactly representable in binary - so a model writing the
    # perfectly reasonable "10.9" would be wrongly accused of inventing it.
    # A fraction restated as a percentage is the same number in different
    # units, not a new one: fraction_of_weight 0.4 read aloud as "40% of
    # the weight". Permitted only for values already between 0 and 1, so
    # this cannot licence arbitrary arithmetic.
    for token in list(allowed):
        value = float(token)
        if 0.0 < value <= 1.0:
            allowed.add(f"{value * 100:g}")

    for token in list(allowed):
        value = float(token)
        allowed.add(f"{math.floor(value):g}")
        allowed.add(f"{math.ceil(value):g}")
        allowed.add(f"{math.floor(value * 10) / 10:g}")
        allowed.add(f"{math.ceil(value * 10) / 10:g}")
    return allowed


def check_explanation(text: str, criterion: dict) -> tuple[bool, str]:
    """
    Reject prose that invents or alters a number, or that oversteps.

    Returns (ok, reason).
    """
    if not text or not text.strip():
        return False, "empty explanation"

    invented = _numbers_in(text) - allowed_numbers(criterion)
    if invented:
        return False, f"states number(s) not present in the data: {sorted(invented)}"

    sentences = [s for s in re.split(r"[.!?]+\s", text.strip()) if s.strip()]
    if len(sentences) > MAX_SENTENCES:
        return False, f"{len(sentences)} sentences, limit {MAX_SENTENCES}"

    # The model must describe, not decide.
    forbidden = re.search(
        r"\b(should be (hired|rejected|shortlisted)|recommend (hiring|rejecting)|"
        r"is the best candidate|must be (hired|rejected)|deserves the (post|job)|"
        r"unqualified|not qualified for|clearly superior)\b", text, re.I)
    if forbidden:
        return False, f"makes a hiring recommendation: {forbidden.group(0)!r}"

    # "Not found" is a statement about OUR knowledge, never about whether
    # the paper exists or whether the candidate was truthful
    # (DESIGN_DECISIONS.md section 6). The model reached for exactly this
    # phrasing unprompted - "could not find any credible evidence of its
    # existence" - so the rule needs enforcing, not just stating.
    overreach = re.search(
        r"(does not exist|did not exist|no evidence of (its |their )?existence|"
        r"never published|did not publish|fabricat\w*|falsif\w*|"
        r"false claim|made up|untruthful|dishonest|lying|misrepresent\w*)",
        text, re.I)
    if overreach:
        return False, (f"asserts non-existence or dishonesty rather than "
                       f"describing what we could confirm: "
                       f"{overreach.group(0)!r}")

    # Claims of a maximum score must match the arithmetic. The model wrote
    # "the full 10.0 points awarded" for a 10.0-of-25 criterion: every
    # number was real, but the claim ABOUT them was false. A numeric guard
    # alone cannot see this.
    points, weight = criterion.get("points"), criterion.get("weight")
    # Distinguish a CLAIM of full marks from a mention of the THRESHOLD.
    #
    # Both are legitimate English and both contain the same words:
    #   claim     : "38 out of 40, earning full marks for this criterion"
    #   threshold : "40% of the 5.0-year threshold for full marks"
    #   threshold : "less than the full marks threshold of 20"
    #
    # An earlier version excluded only "full marks at ...", and rejected
    # three out of four perfectly accurate explanations as a result. The
    # rule is not relaxed - a false claim is still caught - it is made
    # precise, by looking at the words around the phrase.
    if isinstance(points, (int, float)) and weight and points < weight:
        for match in re.finditer(
                r"\b(full (marks|score|points|credit)|"
                r"maximum (score|points|credit)|all available points|"
                r"top marks)\b", text, re.I):
            before = text[max(0, match.start() - 70):match.start()].lower()
            after = text[match.end():match.end() + 25].lower()

            describes_threshold = (
                re.search(r"(threshold|require|requires|required|needed|"
                          r"needs|to reach|to earn|would|less than|"
                          r"short of|below|cap|ceiling|saturat)", before)
                or re.match(r"\s*(at|threshold|requires|would)\b", after)
            )
            if not describes_threshold:
                return False, (f"claims {match.group(0)!r} but scored "
                               f"{points} of {weight}")

    return True, ""


# Plain-English names for the criteria, and for what each one measures.
# The reviewer is a professor reading a candidate, not a developer reading
# a schema: "positions[].start_date" tells them nothing they want to know.
# Kept word-for-word in step with frontend/src/labels.js, so the prose a
# reviewer reads under a heading uses the same name as the heading. The
# internal keys are untouched - they are what the API, the scoring code
# and the override log record.
CRITERION_LABEL = {
    "publications": "Research Publications",
    "research_visibility": "Research Visibility",
    "teaching_experience": "Teaching Experience",
    "industry_experience": "Industry Experience",
    "doctorate": "PhD",
    "postdoctoral": "Postdoctoral Experience",
    "masters": "Master's Degree",
    "bachelors": "Bachelor's Degree",
}

CATEGORY_LABEL = {
    "research": "Research and Related Contributions",
    "experience": "Teaching and Industry Experience",
    "education": "Education",
}


def _sentence(text: str) -> str:
    """
    Upper-case the first letter and nothing else.

    NOT str.capitalize(), which lower-cases the whole remainder and turned
    "awarded by IIT Delhi" into "awarded by iit delhi" in front of a
    reviewer. Proper nouns are the one thing an explanation must not
    mangle: the institution is part of the evidence.
    """
    text = (text or "").strip().rstrip(".")
    if not text:
        return ""
    return text[0].upper() + text[1:] + "."


def _education_sentences(criterion: dict) -> list[str]:
    """
    Why a qualification scored what it did, in a professor's language.

    An education mark is a product of named multipliers, and a number
    with no decomposition is a number nobody can argue with. Each factor
    gets its own clause, with the institution and subject spelled as the
    CV spells them.
    """
    sentences = []

    for row in criterion.get("evidence") or []:
        qualification = row.get("qualification")
        if not qualification:
            continue
        parts = [f"The CV records {qualification}"]
        if row.get("subject"):
            parts.append(f"in {row['subject']}")
        if row.get("organisation"):
            parts.append(f"from {row['organisation']}")
        if row.get("year"):
            parts.append(f"({row['year']})")
        sentences.append(" ".join(parts) + ".")
        break

    for row in (criterion.get("evidence") or [])[:3]:
        if row.get("kind") == "postdoctoral":
            where = f" at {row['organisation']}" if row.get("organisation") else ""
            sentences.append(
                f"Postdoctoral appointment{where} ran from {row.get('from')} "
                f"to {row.get('to')}, {row.get('months')} months.")

    spoken = {
        "qualification": "the stage the qualification has reached",
        "equivalence": "how closely the qualification matches the one "
                       "advertised",
        "institution": "the awarding institution",
        "branch": "how closely the subject fits the department",
    }
    for factor in criterion.get("factors") or []:
        label = spoken.get(factor.get("factor"))
        if label and factor.get("value") is not None:
            sentences.append(
                f"Counted at {factor['value']} for {label}, because "
                f"{factor.get('reason', 'no reason was recorded')}.")

    if not sentences:
        sentences = [_sentence(criterion.get("note", ""))]
    return sentences


def _years(value: float) -> str:
    """'2.0' -> 'two years'. Durations read better as language."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    months = round(number * 12)
    if months <= 0:
        return "no recorded time"
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''}"
    whole, rest = divmod(months, 12)
    if rest == 0:
        return f"{whole} year{'s' if whole != 1 else ''}"
    return (f"{whole} year{'s' if whole != 1 else ''} "
            f"and {rest} month{'s' if rest != 1 else ''}")


def _experience_sentences(criterion: dict) -> list[str]:
    """Describe experience in terms of the roles behind it."""
    rows = [r for r in criterion.get("evidence", []) if r.get("months")]
    if not rows:
        return []

    described = []
    for row in rows[:4]:
        title = row.get("title") or "an unnamed role"
        where = f" at {row['organisation']}" if row.get("organisation") else ""

        # Only state a span when both ends are actually known. Printing
        # "None to None" tells the reviewer nothing and looks like a bug
        # in the very place they are being asked to trust the figures.
        start, end = row.get("from"), row.get("to")
        # Stage 6 writes "present (2026-10-27)" so the as-of date is
        # recorded. Nested inside a span that is itself parenthesised it
        # reads badly, so the date is dropped here - it stays in the audit
        # record, and the criterion's own as_of_date carries it for the UI.
        if isinstance(end, str) and end.startswith("present"):
            end = "present"
        if start and end:
            span = f" ({start} to {end})"
        elif start:
            span = f" (from {start})"
        elif end:
            span = f" (until {end})"
        else:
            span = ""
        duration = (f"{', ' if span else ' '}{_years((row['months']) / 12)}"
                    if row.get("months") else "")

        rate = ""
        if row.get("kind") == "assistantship":
            rate = (f", counted at {int(ASSISTANTSHIP_RATE * 100)}% because it "
                    f"is a teaching assistantship rather than a faculty post")
        described.append(f"{title}{where}{span}{duration}{rate}")

    sentences = ["The CV documents " + "; ".join(described) + "."]
    if len(rows) > 1:
        sentences.append(
            "Where these periods overlap, the overlapping months are counted "
            "once rather than added together.")
    return sentences


def _research_sentences(criterion: dict) -> list[str]:
    """Describe research output, keeping papers and patents distinct."""
    rows = criterion.get("evidence", [])
    papers = [r for r in rows if r.get("kind") != "patent"]
    patents = [r for r in rows if r.get("kind") == "patent"]
    sentences = []

    # Every paper is put in exactly ONE group, by the real reason it earned
    # what it did. The previous wording counted anything with credit as
    # "corroborated" and blamed every zero on the index - so a paper the
    # candidate had labelled "under review" read as one the system failed
    # to find, and a reviewer reasonably concluded the pipeline was
    # losing papers. The cause of a zero is the most important thing this
    # sentence has to get right.
    if papers:
        matched = [r for r in papers if r.get("verification") in (
            "matched", "matched_by_biblio") and (r.get("credit") or 0) > 0]
        partial = [r for r in papers if r not in matched
                   and (r.get("credit") or 0) > 0]
        # Excluded first, so an excluded entry that is ALSO labelled in
        # review is counted once, under the reason that actually decided it.
        excluded = [r for r in papers if r.get("excluded")]
        in_review = [r for r in papers if r.get("status_class") == "in_progress"
                     and not r.get("excluded")]
        other = [r for r in papers if r not in matched + partial + in_review
                 + excluded]

        def n(count, word="paper"):
            return f"{count} {word}{'s' if count != 1 else ''}"

        parts = [f"The CV lists {n(len(papers), 'publication')}"]
        if matched:
            parts.append(f"{len(matched)} found in the OpenAlex index with "
                         f"the candidate among the authors, each earning "
                         f"full credit")
        if partial:
            parts.append(f"{len(partial)} given as a complete citation but "
                         f"not found in the index, earning partial credit - "
                         f"that index does not cover every venue, so this "
                         f"is not a finding against the paper")
        if in_review:
            parts.append(f"{len(in_review)} still under review or submitted, "
                         f"which earn no credit until accepted. That is not "
                         f"a penalty - work in review is normal, it is "
                         f"simply not yet counted")
        if excluded:
            parts.append(f"{len(excluded)} excluded from credit, each with "
                         f"its reason shown in the list")
        if other:
            parts.append(f"{len(other)} that could not be confirmed and "
                         f"{'carries' if len(other) == 1 else 'carry'} no "
                         f"status the CV states")
        sentences.append(parts[0] + ": " + "; ".join(parts[1:]) + "."
                         if len(parts) > 1 else parts[0] + ".")

    # Say where the number sits against the threshold. Without this,
    # "9.19 out of 35 from nine papers" reads as an error rather than as
    # arithmetic, because the reader cannot see what full marks requires.
    threshold = criterion.get("full_marks_at")
    if threshold and criterion.get("measured") is not None:
        sentences.append(
            f"Together these come to {criterion['measured']} of the "
            f"{threshold:g} publication-equivalents needed for full marks.")

    if patents:
        counted = criterion.get("patent_credit_counted", 0)
        granted = sum(1 for r in patents if r.get("status") == "granted")
        sentence = (f"It also lists {len(patents)} patent"
                    f"{'s' if len(patents) != 1 else ''}"
                    f"{f', {granted} of them granted' if granted else ''}, "
                    f"contributing {counted} of the research total")
        sentences.append(sentence + ".")
        sentences.append(
            "Patent records could not be checked against a patent register, "
            "so they rest on the CV alone and are capped so that they "
            "supplement peer-reviewed work rather than replace it.")

    return sentences


def template_explanation(criterion: dict) -> str:
    """
    Deterministic, professor-facing prose. No field names, no JSON paths.

    This is what a reviewer reads when the model is unavailable or its
    wording failed the numeric check, so it has to stand on its own as an
    explanation - not as a debugging trace. The implementation detail it
    used to print ("computed by fixed rules from the fields
    positions[].category, positions[].start_date") told a professor
    nothing they could act on, while implying the answer was somehow
    mechanical rather than evidential.

    The audit record still carries source_fields and every internal label;
    they simply do not belong in front of a reviewer.
    """
    name = CRITERION_LABEL.get(criterion["criterion"],
                               criterion["criterion"].replace("_", " "))
    points, weight = criterion["points"], criterion["weight"]
    measured = criterion.get("measured")
    key = criterion["criterion"]

    opening = f"{name} scored {points} out of {weight}."

    if criterion.get("indeterminate"):
        return (f"{opening} This score is NOT reliable: a lookup service "
                f"could not be reached while the candidate was assessed, so "
                f"part of their record could not be checked. It must not be "
                f"read as a judgement on the candidate. Re-run once the "
                f"service is reachable.")

    if criterion.get("insufficient_evidence"):
        return (f"{opening} The CV did not provide evidence that could be "
                f"scored here, so this criterion contributes nothing to the "
                f"total. That is a statement about what the CV records, not "
                f"a judgement on the candidate.")

    body: list[str] = []
    if key in ("teaching_experience", "industry_experience"):
        body = _experience_sentences(criterion)
        if measured is not None:
            opening = (f"{name} scored {points} out of {weight}, from "
                       f"{_years(measured)} of documented experience.")
    elif key == "publications":
        body = _research_sentences(criterion)
    elif key in ("doctorate", "masters", "bachelors", "postdoctoral"):
        body = _education_sentences(criterion)
    elif key == "research_visibility":
        body = [f"Corroborated publications have attracted {measured} "
                f"citation{'s' if measured != 1 else ''} in total."]

    if criterion.get("unclassified_roles"):
        body.append(
            "One or more roles could not be identified as either a faculty "
            "appointment or a teaching assistantship from the CV; they were "
            "counted at the full rate and need a reviewer's eye.")

    if criterion.get("unscored_positions"):
        titles = ", ".join(
            f"{u['title']}" for u in criterion["unscored_positions"][:3])
        body.append(
            f"The CV documents further roles that could not be categorised "
            f"and therefore count toward no criterion ({titles}). This is a "
            f"gap in what could be read from the CV, not an absence of "
            f"experience, and needs a reviewer's decision.")

    tail = ("The score reflects only what the CV evidences and can be "
            "overridden by a reviewer.")
    return " ".join([opening] + [s for s in body if s.strip()] + [tail])


def explain_criterion(criterion: dict, *, use_llm: bool = True) -> dict:
    """Explain one criterion, with the generated prose verified before use."""
    # A criterion that scored zero because the CV said nothing has a
    # one-sentence answer, and the template already states it exactly:
    # "The CV did not provide evidence that could be scored." Spending a
    # model call to re-word that costs ~10s per criterion and adds a
    # chance of the model inventing a number. On a CV with four empty
    # criteria that is most of the explanation budget spent saying
    # nothing. The LLM is kept for criteria where there IS something to
    # explain.
    if (use_llm and criterion.get("insufficient_evidence")
            and not criterion.get("points")):
        return {"criterion": criterion["criterion"],
                "explanation": template_explanation(criterion),
                "source": "template",
                "llm_rejected_reason": "",
                "llm_skipped": "nothing to explain: no evidence, zero points"}

    if not use_llm:
        return {"criterion": criterion["criterion"],
                "explanation": template_explanation(criterion),
                "source": "template", "llm_rejected_reason": ""}

    # The model sees ONLY the computed criterion - never the CV - so it has
    # nothing to invent new facts from.
    payload = json.dumps(criterion, indent=2, ensure_ascii=False)

    # Hand the model the SAME allow-list the checker will use. Telling it
    # "do not invent numbers" is abstract; showing it the exact numerals it
    # may type is concrete, and costs nothing - the list already exists.
    permitted = sorted(allowed_numbers(criterion), key=lambda t: float(t))
    call = ef.call_ollama(EXPLANATION_PROMPT.format(
        criterion_json=payload,
        allowed_numbers=", ".join(permitted)))

    if not call.ok:
        return {"criterion": criterion["criterion"],
                "explanation": template_explanation(criterion),
                "source": "template",
                "llm_rejected_reason": f"model unavailable: {call.error}"}

    text = (call.data or {}).get("explanation", "")
    ok, reason = check_explanation(text, criterion)
    if not ok:
        return {"criterion": criterion["criterion"],
                "explanation": template_explanation(criterion),
                "source": "template",
                "llm_rejected_reason": reason,
                "rejected_text": text}

    return {"criterion": criterion["criterion"],
            "explanation": text.strip(),
            "source": "llm",
            "model": call.model,
            "prompt_sha": call.prompt_sha,
            "llm_rejected_reason": ""}


def explain_candidate(scored: dict, *, use_llm: bool = True) -> dict:
    """
    Explain every criterion, plus the eligibility outcome.

    ONE CALL PER CRITERION, deliberately.

    Batching all five into a single call was implemented and measured:
    19.2s against roughly 40s, a genuine 2x. It was reverted anyway,
    because it produced this explanation for a criterion that scored
    4.00 of 40 from two credited publications:

        "The CV did not state any credited publications, earning 0 out
         of 4.0 points."

    Every numeral in that sentence was legitimately in the data, so the
    numeric guard passed it. Given several criteria at once the model
    attributed one criterion's facts to another, and a check that
    validates numbers cannot catch a false claim assembled from true
    ones. A faster explanation that misinforms the reviewer is worth
    less than a slower one that does not.
    """
    explanations = [explain_criterion(c, use_llm=use_llm)
                    for c in scored.get("criteria", [])]

    gate = scored.get("eligibility", {})
    gate_text = (
        f"Eligibility: {gate.get('outcome')}. {gate.get('reason', '')}. "
        f"This is a gate, assessed separately from the score and affecting "
        f"no part of it; a reviewer may override it. Requirement applied: "
        f"{gate.get('requirement', '')}"
    )

    return {
        "candidate_id": scored.get("candidate_id"),
        "eligibility_explanation": gate_text,
        "criterion_explanations": explanations,
        "llm_used": any(e["source"] == "llm" for e in explanations),
        "explanations_rejected": sum(
            1 for e in explanations if e.get("llm_rejected_reason")),
    }


def main(argv: list[str]) -> int:
    use_llm = "--no-llm" not in argv
    out_dir = Path(__file__).resolve().parent.parent / "output"
    files = sorted(out_dir.glob("Candidate_*.scored.json"))
    if not files:
        print(f"No *.scored.json in {out_dir}. Run scoring.py first.")
        return 1

    print(f"Stage 7a: explanations  (llm {'on' if use_llm else 'OFF'})\n")
    for path in files:
        stem = path.name.replace(".scored.json", "")
        scored = json.loads(path.read_text(encoding="utf-8"))
        result = explain_candidate(scored, use_llm=use_llm)
        (out_dir / f"{stem}.explained.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{stem}: {len(result['criterion_explanations'])} explained, "
              f"{result['explanations_rejected']} rejected and replaced")
        for e in result["criterion_explanations"]:
            if e.get("llm_rejected_reason"):
                print(f"    REJECTED {e['criterion']}: {e['llm_rejected_reason']}")

    print(f"\nWrote *.explained.json to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
