"""
STAGE 3 TEST - compare extracted JSON against hand-written ground truth.

Reports a field-by-field match rate. This measures EXTRACTION ACCURACY only;
it says nothing about whether the model invented an evidence span (Stage 4)
or whether a publication is real (Stage 5).

Comparison is deliberately forgiving about FORMATTING and strict about
FACTS, because "Master of Technology" vs "M.Tech" is not an error worth
failing, while 2025 vs 2024 is.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

SCALAR_FIELDS = (
    "full_name",
    "email",
    "orcid",
    "scholar_url",
    "highest_degree",
    "phd_status",
    "phd_institution",
    "phd_start_year",
    "phd_year",
    "masters_degree",
    "masters_institution",
    "masters_year",
    "net_qualified",
    "research_area",
)

# Position titles get a trailing qualifier on some CVs and not others:
# "Teaching Assistant - Computer Programming" vs "Teaching Assistant".
# The course or department is not part of the role, so it is stripped
# before comparison (user decision, 2026-10-14).
TITLE_SUFFIX = re.compile(r"\s*[-–—|(].*$")


def strip_title_suffix(title) -> str:
    return TITLE_SUFFIX.sub("", str(title or "")).strip()


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def normalise_date(value):
    """
    Reduce a CV date to (year, month) so formatting cannot count as error.

    The prompt asks for "YYYY-MM", and the model sometimes returns
    "Aug 2025" instead. That is a deviation from the requested format, not
    a wrong fact - the same month is being reported either way. Stage 6
    has to parse both regardless, so the comparator measures the DATE, and
    the format deviation is recorded as a defect rather than as an
    extraction error.
    """
    if value in (None, ""):
        return None
    text = str(value).strip().lower()

    iso = re.match(r"^(\d{4})-(\d{1,2})$", text)
    if iso:
        return int(iso.group(1)), int(iso.group(2))

    year_only = re.match(r"^(\d{4})$", text)
    if year_only:
        return int(year_only.group(1)), None

    named = re.match(r"^([a-z]{3,9})\.?\s+(\d{4})$", text)
    if named:
        return int(named.group(2)), MONTHS.get(named.group(1)[:3])

    named_rev = re.match(r"^(\d{4})\s+([a-z]{3,9})$", text)
    if named_rev:
        return int(named_rev.group(1)), MONTHS.get(named_rev.group(2)[:3])

    return ("raw", text)


def dates_match(expected, actual) -> bool:
    return normalise_date(expected) == normalise_date(actual)


def norm(value) -> str:
    """Normalise for comparison: case, accents, punctuation, whitespace."""
    if value is None:
        return "<null>"
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strings_match(expected, actual) -> bool:
    """Equal after normalisation, or one contains the other."""
    e, a = norm(expected), norm(actual)
    if e == a:
        return True
    if e == "<null>" or a == "<null>":
        return False
    # Institution names get abbreviated constantly ("NIT Surat" vs the full
    # "Sardar Vallabhbhai National Institute of Technology Surat").
    return len(e) > 3 and len(a) > 3 and (e in a or a in e)


def compare_scalars(truth: dict, got: dict, lenient: set[str]) -> list[dict]:
    rows = []
    for fieldname in SCALAR_FIELDS:
        expected = truth.get(fieldname)
        wrapped = got.get(fieldname)
        # The model returns {"value":..., "evidence":...}; tolerate a bare value.
        actual = wrapped.get("value") if isinstance(wrapped, dict) else wrapped

        if isinstance(expected, int) or isinstance(actual, int):
            ok = expected == actual
        else:
            ok = strings_match(expected, actual)

        rows.append({
            "field": fieldname,
            "expected": expected,
            "actual": actual,
            "ok": ok,
            "lenient": fieldname in lenient,
        })
    return rows


def compare_publications(truth: list, got: list, lenient: bool) -> dict:
    """Match on title, then check the other fields of matched pairs."""
    matched, detail = 0, []
    remaining = list(got)

    for expected in truth:
        hit = None
        for candidate in remaining:
            if strings_match(expected.get("title"), candidate.get("title")):
                hit = candidate
                break
        if hit is None:
            detail.append({"title": expected.get("title"), "found": False})
            continue

        remaining.remove(hit)
        matched += 1
        fields = {}
        for key in ("year", "venue", "doi", "status"):
            exp, act = expected.get(key), hit.get(key)
            fields[key] = (exp == act) if key in ("year",) else strings_match(exp, act)
        exp_authors = [norm(a) for a in expected.get("authors", [])]
        act_authors = [norm(a) for a in hit.get("authors", []) or []]
        fields["authors"] = exp_authors == act_authors
        detail.append({"title": expected.get("title"), "found": True, "fields": fields})

    return {
        "expected_count": len(truth),
        "actual_count": len(got),
        "matched": matched,
        "spurious": len(remaining),
        "detail": detail,
        "lenient": lenient,
    }


def compare_positions(truth: list, got: list) -> dict:
    """
    Match positions by (title-without-suffix, organisation), then compare
    their remaining fields. Organisation is compared STRICTLY, including
    null: where the CV prints no employer the correct answer is null, and
    inferring one from context counts as wrong (user decision, 2026-10-14).
    """
    remaining = list(got)
    rows = []
    format_notes: list[str] = []

    for expected in truth:
        exp_title = strip_title_suffix(expected.get("title"))
        hit = None
        for candidate in remaining:
            if strings_match(exp_title, strip_title_suffix(candidate.get("title"))):
                hit = candidate
                break

        if hit is None:
            rows.append({"title": exp_title, "found": False, "wrong": []})
            continue

        remaining.remove(hit)
        wrong = []
        # Strict null-aware organisation comparison.
        exp_org, act_org = expected.get("organisation"), hit.get("organisation")
        if (exp_org is None) != (act_org in (None, "")):
            wrong.append(f"organisation (expected {exp_org!r}, got {act_org!r})")
        elif exp_org is not None and not strings_match(exp_org, act_org):
            wrong.append(f"organisation (expected {exp_org!r}, got {act_org!r})")

        for key in ("start_date", "end_date"):
            exp, act = expected.get(key), hit.get(key)
            if (exp is None) != (act in (None, "")):
                wrong.append(f"{key} (expected {exp!r}, got {act!r})")
            elif exp is not None and not dates_match(exp, act):
                wrong.append(f"{key} (expected {exp!r}, got {act!r})")
            elif exp is not None and str(exp) != str(act):
                # Same date, wrong shape. Tracked separately so it does not
                # masquerade as an extraction error, but is not invisible.
                format_notes.append(f"{key} format {act!r} (asked for {exp!r})")

        exp_cat, act_cat = expected.get("category"), hit.get("category")
        if (exp_cat is None) != (act_cat in (None, "")):
            wrong.append(f"category (expected {exp_cat!r}, got {act_cat!r})")
        elif exp_cat is not None and not strings_match(exp_cat, act_cat):
            wrong.append(f"category (expected {exp_cat!r}, got {act_cat!r})")

        if bool(expected.get("is_current")) != bool(hit.get("is_current")):
            wrong.append("is_current")

        rows.append({"title": exp_title, "found": True, "wrong": wrong})

    return {"rows": rows, "spurious": len(remaining), "format_notes": format_notes,
            "expected": len(truth), "actual": len(got)}


def report(stem: str, truth: dict, record: dict) -> tuple[int, int]:
    lenient = set(truth.get("_lenient_fields", []))
    profile = record.get("profile", {})

    print("=" * 74)
    print(f"{stem}")
    print("=" * 74)

    rows = compare_scalars(truth, profile, lenient)
    strict = [r for r in rows if not r["lenient"]]
    hits = sum(1 for r in strict if r["ok"])

    print(f"\n  {'field':<22}{'expected':<26}{'got':<26}")
    print("  " + "-" * 72)
    for r in rows:
        mark = "ok " if r["ok"] else ("~  " if r["lenient"] else "MISS")
        exp = str(r["expected"])[:24]
        act = str(r["actual"])[:24]
        print(f"  {mark} {r['field']:<21}{exp:<26}{act:<26}")

    print(f"\n  scalar fields (strict): {hits}/{len(strict)} = "
          f"{100*hits/len(strict):.0f}%")

    pubs = compare_publications(
        truth.get("publications", []),
        record.get("publications", []),
        "publications" in lenient,
    )
    print(f"\n  publications: expected {pubs['expected_count']}, "
          f"got {pubs['actual_count']}, matched {pubs['matched']}, "
          f"spurious {pubs['spurious']}")
    for d in pubs["detail"]:
        if not d["found"]:
            print(f"    MISSING: {str(d['title'])[:60]}")
        else:
            bad = [k for k, v in d["fields"].items() if not v]
            status = "all fields ok" if not bad else f"wrong: {', '.join(bad)}"
            print(f"    found:   {str(d['title'])[:48]:<50} {status}")

    pos = compare_positions(truth.get("positions", []),
                            profile.get("positions", []) or [])
    exact = sum(1 for r in pos["rows"] if r["found"] and not r["wrong"])
    print(f"\n  positions: expected {pos['expected']}, got {pos['actual']}, "
          f"{exact} fully correct, {pos['spurious']} spurious")
    for r in pos["rows"]:
        if not r["found"]:
            print(f"    MISSING: {r['title']}")
        elif r["wrong"]:
            print(f"    found:   {r['title']:<32} wrong: {'; '.join(r['wrong'])[:70]}")
    for note in pos.get("format_notes", []):
        print(f"    note:    {note}")

    return hits, len(strict)


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    gt_dir, out_dir = root / "data" / "ground_truth", root / "output"

    gt_files = [Path(a) for a in argv] if argv else sorted(gt_dir.glob("Candidate_*.json"))
    if not gt_files:
        print(f"No ground truth files in {gt_dir}")
        return 1

    total_hits = total_fields = 0
    for gt_path in gt_files:
        stem = gt_path.stem
        extracted = out_dir / f"{stem}.extracted.json"
        if not extracted.is_file():
            print(f"{stem}: no {extracted.name} - run extract_fields.py first")
            continue
        truth = json.loads(gt_path.read_text(encoding="utf-8"))
        record = json.loads(extracted.read_text(encoding="utf-8"))
        hits, fields = report(stem, truth, record)
        total_hits += hits
        total_fields += fields

    if total_fields:
        print("\n" + "=" * 74)
        print(f"OVERALL scalar accuracy: {total_hits}/{total_fields} = "
              f"{100*total_hits/total_fields:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
