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

MAX_SENTENCES = 5

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


def template_explanation(criterion: dict) -> str:
    """
    Deterministic fallback, used when the model's prose fails the check or
    the model is unavailable.

    An audit file with a plain, correct sentence is strictly better than one
    with fluent prose nobody verified.
    """
    name = criterion["criterion"].replace("_", " ")
    points, weight = criterion["points"], criterion["weight"]
    measured, unit = criterion["measured"], criterion["unit"]

    if criterion.get("insufficient_evidence"):
        return (f"The {name} criterion scored {points} out of {weight}. "
                f"The CV did not provide evidence that could be scored for "
                f"this criterion, so it contributes nothing to the total. "
                f"Detail recorded: {criterion.get('note', 'none')}.")

    return (f"The {name} criterion scored {points} out of {weight}, from a "
            f"measured {measured} {unit}. This was computed by fixed rules "
            f"from the fields {', '.join(criterion.get('source_fields', []))}. "
            f"Detail recorded: {criterion.get('note', 'none')}.")


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
