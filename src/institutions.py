"""
Institution classification - tiers, normalisation, and honest uncertainty.

WHY THIS MODULE EXISTS SEPARATELY FROM SCORING
    Institution standing is the single most contestable input in the whole
    rubric. A hiring committee can reasonably disagree with any tier here,
    and they must be able to SEE and CHANGE it without reading Python. So
    the tier list is data (config/institutions.json), this module is only
    the matcher, and scoring.py consumes the result without knowing a
    single institution name.

THREE THINGS KEPT APART, deliberately
    1. INSTITUTION STANDING  - handled here.
    2. QUALIFICATION VALIDITY - whether the degree is real and completed.
    3. BRANCH RELEVANCE       - whether the subject fits the post.
    Conflating any two of these is how a rubric starts penalising a
    candidate twice for one fact. Validity lives in validate.py, relevance
    lives in degrees.py, and neither reads this module.

THE RULE ON NOT KNOWING
    An institution this configuration does not recognise is 'unknown'. It
    earns the UNKNOWN multiplier and is FLAGGED for a human. It is never
    silently scored as poor, because "we have no entry for this college"
    and "this college is weak" are different statements and only the first
    one is supported by evidence.

    A SECOND kind of not-knowing sits between the two: the family is
    certain but the campus is not. "IIT" with no campus named is
    definitely an IIT, and the tier of an IIT now depends on which one it
    is. Such a name takes the tier the approved rule gives it AND carries
    needs_review, set by the matching pattern in the configuration. See
    the tier_2 catch-alls in config/institutions.json.

ONE TIER PER QUALIFICATION
    classify() is called exactly once per qualification by scoring.py -
    once for the doctorate, once for each of bachelors and masters - and
    the multiplier it returns is applied once, to that category's
    fraction. Institution standing therefore cannot stack with itself or
    carry a qualification past its own category weight.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "institutions.json"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalise(name: str | None) -> str:
    """
    Fold an institution string to a comparable form.

    Lowercase, strip accents, ampersand to 'and', drop punctuation,
    collapse whitespace. "I.I.T. Bombay", "IIT-Bombay" and "iit bombay"
    all converge, which is the whole point: a CV prints one of them and
    the configuration cannot list all three.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _lower_first(text: str) -> str:
    """
    Lowercase the first character only, leaving acronyms intact.

    The tier labels carry acronyms - IIT, NIT, IISc - and str.lower()
    would flatten them into unreadable prose. These labels are dropped
    mid-sentence into a reviewer's explanation, so the opening letter has
    to come down and nothing else may move.
    """
    return text[:1].lower() + text[1:] if text else ""


def _collapse_initialisms(text: str) -> str:
    """
    Rejoin a spelled-out acronym that punctuation-stripping took apart.

    "I.I.T. Delhi" normalises to "i i t delhi", and no pattern for \\biit\\b
    can match that. Runs of TWO OR MORE single letters are rejoined, so
    "i i t delhi" becomes "iit delhi" while "b tech" is left alone - a run
    of one is just a word that happens to be short.
    """
    out, run = [], []
    for token in text.split():
        if len(token) == 1 and token.isalpha():
            run.append(token)
            continue
        if len(run) >= 2:
            out.append("".join(run))
        else:
            out.extend(run)
        run = []
        out.append(token)
    if len(run) >= 2:
        out.append("".join(run))
    else:
        out.extend(run)
    return " ".join(out)


@lru_cache(maxsize=1)
def config() -> dict:
    """The live configuration, read once and cached."""
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def reload_config() -> dict:
    """Drop the cache. For tests and for an admin edit without a restart."""
    config.cache_clear()
    return config()


def _expand_aliases(text: str) -> tuple[str, list[str]]:
    """
    Replace known abbreviations, former names and merged names.

    Longest key first, so "netaji subhas institute of technology" is not
    half-replaced by a shorter key that happens to be a prefix.
    """
    aliases = {k: v for k, v in config().get("aliases", {}).items()
               if not k.startswith("_")}
    applied = []
    for key in sorted(aliases, key=len, reverse=True):
        pattern = r"\b" + re.escape(key) + r"\b"
        if re.search(pattern, text):
            text = re.sub(pattern, aliases[key], text)
            applied.append(f"{key} -> {aliases[key]}")
    return re.sub(r"\s+", " ", text).strip(), applied


def _barred_tiers(text: str) -> dict[str, str]:
    """
    Tiers a lookalike name may NOT claim.

    "National Institute of Fashion Technology" contains neither 'nit' as a
    word nor 'national institute of technology' as a phrase, so it would
    not match anyway - but BIT Mesra against BITS Pilani is a real
    collision, and relying on a regex accident to prevent it is not a
    safeguard. This makes the exclusion explicit and reviewable.

    An entry bars one tier via 'not_tier' or several via 'not_tiers'. Both
    spellings are accepted: tier 2 now ends in a family-wide IIT/NIT
    catch-all, so a lookalike has to be kept out of two tiers rather than
    one, and the older single-tier entries must keep working.
    """
    barred = {}
    for entry in config().get("never_match", {}).get("entries", []):
        if not re.search(entry["match"], text):
            continue
        tiers = entry.get("not_tiers") or [entry["not_tier"]]
        for tier_name in tiers:
            barred[tier_name] = entry["why"]
    return barred


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(name: str | None) -> dict:
    """
    Classify one institution.

    Returns a dict with:
      tier          'tier_1' | 'tier_2' | 'tier_3' | 'unknown' | 'absent'
      multiplier    the fraction of a degree's category marks earned
      label         what it matched as, for the reviewer
      reason        why, in a sentence a professor can read
      needs_review  True when a human must confirm the classification
      normalised    the folded string actually matched against

    'absent' is distinct from 'unknown': the first means the CV named no
    institution, the second means it named one we do not recognise.
    """
    multipliers = config()["multipliers"]
    raw = normalise(name)

    if not raw:
        return {
            "tier": "absent",
            "multiplier": multipliers["unknown"],
            "label": None,
            "reason": "the CV names no awarding institution for this degree",
            "needs_review": True,
            "normalised": "",
            "aliases_applied": [],
        }

    text, applied = _expand_aliases(raw)
    # Both spellings are tried: "iit delhi" as printed, and the rejoined
    # form for CVs that write "I.I.T. Delhi". Matching only the collapsed
    # form would risk fusing letters that were separate words.
    candidates = [text]
    collapsed = _collapse_initialisms(text)
    if collapsed != text:
        collapsed, collapsed_applied = _expand_aliases(collapsed)
        applied = applied + collapsed_applied
        candidates.append(collapsed)

    barred = {}
    for form in candidates:
        barred.update(_barred_tiers(form))

    for tier_name, tier in config()["tiers"].items():
        if tier_name in barred:
            continue
        for entry in tier["patterns"]:
            if not any(re.search(entry["match"], form) for form in candidates):
                continue
            # A pattern may be confident about the FAMILY but not the
            # campus - "an IIT" with no campus named, say. The tier then
            # still follows the approved rule, because leaving it
            # unrecognised would drop a statutory institute to the base
            # rate, but the entry asks a human to confirm instead of the
            # list deciding silently. Requirement: an institution that
            # cannot be confidently identified is flagged, not guessed.
            uncertain = bool(entry.get("needs_review"))
            # Only the first letter is lowered, not the whole label:
            # .lower() on "First-generation IITs, top NITs, IISc" produces
            # "iits, top nits, iisc", and a professor reading that cannot
            # tell which institutes are meant. The clause reads
            # "... because recognised as X - <label>", so it needs a
            # lowercase opening and nothing else changed.
            reason = (f"recognised as {entry['label']} - "
                      f"{_lower_first(tier['label'])}")
            if uncertain and entry.get("review_why"):
                reason = entry["review_why"]
            return {
                "tier": tier_name,
                "multiplier": multipliers[tier_name],
                "label": entry["label"],
                "reason": reason,
                "needs_review": uncertain,
                "normalised": text,
                "aliases_applied": applied,
            }

    reason = ("not listed in the approved institution configuration, so it "
              "is scored at the base rate and flagged for you to classify. "
              "This is a gap in our list, not a judgement on the institution")
    if barred:
        reason = (list(barred.values())[0] + "; "
                  "no other tier matched, so it is flagged for you to classify")

    return {
        "tier": "unknown",
        "multiplier": multipliers["unknown"],
        "label": None,
        "reason": reason,
        "needs_review": True,
        "normalised": text,
        "aliases_applied": applied,
    }


def visible_rubric() -> dict:
    """
    The classification criteria, for an administrator to read.

    Serves the spec requirement that the criteria and scoring values be
    visible to maintainers. Patterns are included: an admin who cannot see
    the matching rule cannot audit a classification they disagree with.
    """
    live = config()
    return {
        "version": live["version"],
        "approved_by": live.get("approved_by"),
        "approved_on": live.get("approved_on"),
        "config_file": str(CONFIG_PATH),
        "tier_basis": live.get("tier_basis"),
        "multipliers": live["multipliers"],
        "tiers": {
            name: {
                "label": tier["label"],
                "rationale": tier["rationale"],
                "pattern_count": len(tier["patterns"]),
                "patterns": [p["match"] for p in tier["patterns"]],
                "labels": sorted({p["label"] for p in tier["patterns"]}),
            }
            for name, tier in live["tiers"].items()
        },
        "alias_count": len([k for k in live.get("aliases", {})
                            if not k.startswith("_")]),
        "never_match": live.get("never_match", {}).get("entries", []),
        "unknown_policy": (
            "An institution absent from this configuration earns the "
            "'unknown' multiplier and is flagged for human review. It is "
            "never silently treated as low-tier."),
        "disclaimer": ("DEMO/RESEARCH tiers for a student project. Not "
                       "approved recruitment policy."),
    }
