"""
STAGE 5 - publication lookup against OpenAlex.

Implements the contract written down in schema.py section 5, before any of
this code existed, so the thresholds could not be tuned to flatter the
results.

WHAT THIS STAGE CAN AND CANNOT ESTABLISH
  It can establish that a paper with a given title EXISTS and is indexed,
  and that a name resembling the candidate's appears in its author list.

  It CANNOT establish that this candidate wrote it. Personal names are not
  identifiers. "Ayush Kumar" and "Arpit Singh Yadav" are common enough that
  a homonym in the same broad field is entirely plausible. Only an ORCID iD
  binds a person to a record, and none of the four sample CVs carry one.

  So every outcome here is ASSISTED MATCHING WITH A CONFIDENCE LEVEL, never
  proof of authorship, and the wording in the output says so. The OpenAlex
  URL travels with every verdict precisely so a human can check it.

NETWORK AND CACHING
  Responses are cached on disk under output/openalex_cache/. A cached run
  is reproducible, costs nothing, and lets the tests run without touching
  the network. Delete the directory to force a refresh.

  OpenAlex is free and needs no key, but asks for a mailto in the query so
  they can contact heavy users - that is the polite pool, and it is also
  faster.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402

OPENALEX_WORKS = "https://api.openalex.org/works"

# OpenAlex asks for a contact address in the query string. No key needed.
# Replace with your own address if you run this heavily.
MAILTO = "faculty-recruitment-miniproject@example.org"

CACHE_DIR = Path(__file__).resolve().parent.parent / "output" / "openalex_cache"
# (connect, read) - see the note in extract_fields.py. OpenAlex is a
# remote service, so a connect that does not land quickly will not land.
REQUEST_TIMEOUT = (5, 30)
RATE_LIMIT_SECONDS = 0.2   # be a good citizen on a free, unauthenticated API
MAX_CANDIDATES = 8         # OpenAlex results to consider per title
FALLBACK_PAGE_SIZE = 25    # larger pages time out on the author+year filter

HONORIFICS = {"dr", "prof", "professor", "mr", "ms", "mrs", "shri", "smt"}


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def norm_title(text: str) -> str:
    """Lowercase, de-accent, drop punctuation, collapse spaces."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = text.encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, norm_title(a), norm_title(b)).ratio(), 3)


def parse_name(raw: str) -> tuple[str, list[str]]:
    """
    Split a personal name into (surname, given-name tokens).

    Handles the formats that actually turn up:
      "Dr. Shiva Dixit"          -> ("dixit",  ["shiva"])
      "Yadav, Arpit Singh"       -> ("yadav",  ["arpit", "singh"])
      "A. S. Yadav"              -> ("yadav",  ["a", "s"])
      "Shiva Dixit"              -> ("dixit",  ["shiva"])
    """
    text = unicodedata.normalize("NFKD", str(raw or ""))
    text = text.encode("ascii", "ignore").decode().lower()

    if "," in text:
        # "Surname, Given Names" - the comma tells us which part is which.
        surname_part, _, given_part = text.partition(",")
        surname_tokens = re.findall(r"[a-z]+", surname_part)
        given_tokens = re.findall(r"[a-z]+", given_part)
        given_tokens = [t for t in given_tokens if t not in HONORIFICS]
        if surname_tokens:
            return surname_tokens[-1], given_tokens

    tokens = [t for t in re.findall(r"[a-z]+", text) if t not in HONORIFICS]
    if not tokens:
        return "", []
    return tokens[-1], tokens[:-1]


def names_match(candidate: str, indexed: str) -> bool:
    """
    Does `indexed` (an OpenAlex author name) plausibly denote `candidate`?

    Rule from schema.py: the surname must match exactly, and the first
    given name must either match in full or agree on its initial. The
    initial rule is what lets "A. S. Yadav" match "Arpit Singh Yadav" -
    journals abbreviate given names constantly.
    """
    c_surname, c_given = parse_name(candidate)
    i_surname, i_given = parse_name(indexed)

    if not c_surname or c_surname != i_surname:
        return False
    if not c_given or not i_given:
        # Surname matched and one side gave no given name. Too weak to
        # accept on its own.
        return False

    c_first, i_first = c_given[0], i_given[0]
    if c_first == i_first:
        return True
    # Initial agreement, in either direction.
    if len(c_first) == 1 or len(i_first) == 1:
        return c_first[0] == i_first[0]
    return False


# ---------------------------------------------------------------------------
# OpenAlex access, with a disk cache
# ---------------------------------------------------------------------------

def _cache_path(url: str) -> Path:
    return CACHE_DIR / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.json"


# Characters that break OpenAlex's search parser rather than narrowing it.
#
# MEASURED on Candidate_06's "The Rise Of Environmental Wellbeing: HRM's
# Evolving Role In Sustainable Workspaces And Mental Health":
#
#   sent with the apostrophe  ->  14 results, best similarity 0.293
#   apostrophe removed        -> 225 results, best similarity 1.000 (exact)
#
# Curly or straight made no difference - the apostrophe itself is the
# problem. The paper was in the index the whole time and we reported
# "not found", which is precisely the conflation this project tries to
# avoid: our query failing is not the work being absent.
#
# Only the QUERY is sanitised. Similarity is still scored against the
# title as printed on the CV, so this buys recall without loosening the
# match test that guards against false positives.
QUERY_NOISE = re.compile(r"[^\w\s-]", re.UNICODE)


def search_query(title: str) -> str:
    """Punctuation-free search terms for OpenAlex."""
    cleaned = QUERY_NOISE.sub(" ", unicodedata.normalize("NFKC", str(title or "")))
    return re.sub(r"\s+", " ", cleaned).strip()


def openalex_search(title: str, *, use_cache: bool = True) -> tuple[dict, str, str]:
    """
    Search OpenAlex for a title.

    Returns (payload, url, error). On failure payload is {} and error
    explains why - the caller turns that into a "lookup_failed" verdict
    rather than a zero, so an outage can never be mistaken for an absent
    publication.
    """
    query = urlencode({
        "search": search_query(title),
        "per-page": MAX_CANDIDATES,
        "mailto": MAILTO,
    })
    url = f"{OPENALEX_WORKS}?{query}"
    cached = _cache_path(url)

    if use_cache and cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8")), url, ""

    try:
        time.sleep(RATE_LIMIT_SECONDS)
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return {}, url, f"{type(exc).__name__}: {exc}"

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(payload), encoding="utf-8")
    return payload, url, ""


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Bibliographic fallback - for titles that changed before publication
# ---------------------------------------------------------------------------
# Parses "Physical Chemistry Chemical Physics, 26, 25590 (2024)." into
# volume 26 and first page 25590. Handles the shapes that appear in the
# sample CVs:
#     "26, 25590 (2024)."            -> vol 26, page 25590
#     "148 (2), 23002 (2024)."       -> vol 148, page 23002
#     "181, 114639, (2024)."         -> vol 181, page 114639
#     "35 (12), (2025)."             -> vol 35, no page
BIBLIO_YEAR_TAIL = re.compile(r"\(\s*(?:19|20)\d{2}\s*\)\s*\.?\s*$")
BIBLIO_ISSUE = re.compile(r"\(\s*\d+\s*\)")


def parse_biblio(evidence: str) -> tuple[str | None, str | None]:
    """Return (volume, first_page) read off a citation line, best effort."""
    if not evidence:
        return None, None
    text = BIBLIO_YEAR_TAIL.sub("", str(evidence).strip())
    text = BIBLIO_ISSUE.sub(" ", text)            # drop the issue number
    numbers = re.findall(r"(?<![\w.])(\d{1,7})(?![\w.])", text)
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], None
    return numbers[0], numbers[-1]


def biblio_fallback(pub: dict, candidate_name: str, *,
                    use_cache: bool = True) -> tuple[dict | None, str, str]:
    """
    Second attempt when the title search found nothing.

    Queries by author and year - the two things that do NOT change when a
    title is rewritten - then confirms the hit Python-side on venue,
    volume and first page. OpenAlex rejects a venue filter combined with
    these (HTTP 400), so the venue check happens here rather than in the
    query.

    Returns (work, query_url, reason-if-no-match).
    """
    year = pub.get("year")
    if not candidate_name or not year:
        return None, "", "no author/year available for a fallback lookup"

    # Strip honorifics before querying. OpenAlex indexes "Shiva Dixit", not
    # "Dr. Shiva Dixit", and sending the title silently returned nothing -
    # a failure that looked identical to "the paper is not indexed".
    surname, given = parse_name(candidate_name)
    query_name = " ".join(given + [surname]).strip()
    if not query_name:
        return None, "", "candidate name could not be parsed for a lookup"

    cv_volume, cv_page = parse_biblio(pub.get("evidence") or "")
    cv_venue = pub.get("venue") or ""
    if not cv_page and not (cv_volume and cv_venue):
        return None, "", "citation carries no volume/page to match on"

    query = urlencode({
        # Year tolerance matters here too: online-first and issue years differ.
        "filter": f"publication_year:{int(year)},"
                  f"raw_author_name.search:{query_name}",
        # 50 made OpenAlex return HTTP 504 "query took too long" on this
        # filter combination. The author+year filter is already narrow -
        # this candidate's 2024 papers number single digits - so a smaller
        # page is both sufficient and reliable.
        "per-page": FALLBACK_PAGE_SIZE,
        "mailto": MAILTO,
    })
    url = f"{OPENALEX_WORKS}?{query}"
    cached = _cache_path(url)

    if use_cache and cached.is_file():
        payload = json.loads(cached.read_text(encoding="utf-8"))
    else:
        payload = None
        for attempt in range(2):          # one retry: 504s here are transient
            try:
                time.sleep(RATE_LIMIT_SECONDS * (1 + attempt))
                response = requests.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                payload = response.json()
                break
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"[:120]
        if payload is None:
            return None, url, f"fallback request failed ({last})"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload), encoding="utf-8")

    for work in payload.get("results", []) or []:
        biblio = work.get("biblio") or {}
        venue = ((work.get("primary_location") or {}).get("source") or {})
        venue_name = venue.get("display_name") or ""

        page_ok = bool(cv_page) and str(biblio.get("first_page") or "") == str(cv_page)
        volume_ok = bool(cv_volume) and str(biblio.get("volume") or "") == str(cv_volume)
        venue_ok = bool(cv_venue) and title_similarity(cv_venue, venue_name) >= 0.80
        author_ok = any(names_match(candidate_name, a) for a in _authors_of(work))

        # Deliberately strict: the author must be confirmed, the page must
        # match exactly, and either the volume or the venue must agree too.
        # Journal + volume + first page is close to a primary key, so a
        # looser rule here would start inventing matches.
        if author_ok and page_ok and (volume_ok or venue_ok):
            return work, url, ""

    return None, url, "no record matched on author + year + volume/page"


def _authors_of(work: dict) -> list[str]:
    names = []
    for authorship in work.get("authorships", []) or []:
        author = (authorship.get("author") or {}).get("display_name")
        raw = authorship.get("raw_author_name")
        for name in (author, raw):
            if name and name not in names:
                names.append(name)
    return names


def verify_publication(pub: dict, candidate_name: str, *,
                       use_cache: bool = True) -> dict:
    """
    Verify one publication. Returns the evidence record schema.py requires.

    The decision follows schema.VERDICT_RULES exactly:
      matched            title >= EXACT, author confirmed, year agrees
      weak_match         author confirmed, but title only CLOSE or year off
      unconfirmed_author title >= EXACT but the candidate is not an author
      not_found          nothing above CLOSE, or CLOSE with no author match
      lookup_failed      the request itself failed
    """
    title = pub.get("title") or ""
    cv_year = pub.get("year")

    payload, url, error = openalex_search(title, use_cache=use_cache)

    record = {
        "query_url": url,
        "openalex_id": None,
        "matched_title": None,
        "title_similarity": 0.0,
        "year_cv": cv_year,
        "year_openalex": None,
        "authors_openalex": [],
        "author_match": None,
        "outcome": "not_found",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "",
    }

    if error:
        record["outcome"] = "lookup_failed"
        record["note"] = (f"OpenAlex request failed ({error}). Scored as "
                          f"INDETERMINATE, never as zero.")
        return record

    results = payload.get("results", []) or []

    # Best candidate by title similarity.
    best, best_sim = None, 0.0
    for work in results:
        sim = title_similarity(title, work.get("display_name") or "")
        if sim > best_sim:
            best, best_sim = work, sim

    # TITLE SEARCH FAILED -> try the bibliographic coordinates before
    # concluding anything. "Our title query found nothing" is not the same
    # claim as "this publication does not exist".
    if best is None or best_sim < schema.TITLE_SIMILARITY_CLOSE:
        work, fb_url, why = biblio_fallback(pub, candidate_name,
                                            use_cache=use_cache)
        if work is not None:
            biblio = work.get("biblio") or {}
            authors = _authors_of(work)
            record.update({
                "openalex_id": work.get("id"),
                "matched_title": work.get("display_name"),
                "title_similarity": title_similarity(
                    title, work.get("display_name") or ""),
                "year_openalex": work.get("publication_year"),
                "authors_openalex": authors,
                "author_match": next(
                    (a for a in authors if names_match(candidate_name, a)), None),
                "cited_by_count": work.get("cited_by_count"),
                "doi": work.get("doi"),
                "outcome": "matched_by_biblio",
                "fallback_query_url": fb_url,
                "matched_volume": biblio.get("volume"),
                "matched_first_page": biblio.get("first_page"),
                "note": (
                    "TITLE DIFFERS. No title search matched, but the journal, "
                    "volume, first page, year and author list all agree, which "
                    "identifies an article more tightly than a title does. The "
                    "paper appears to have been published under a different "
                    "title from the one on the CV - ordinary between submission "
                    "and publication. Please compare the two titles."
                ),
            })
            return record

        record["title_similarity"] = best_sim
        record["fallback_query_url"] = fb_url
        record["note"] = (
            f"no title match (best {best_sim}); bibliographic fallback also "
            f"found nothing ({why}). This means we could not corroborate the "
            f"entry - NOT that the publication does not exist."
        )
        return record

    if not results:
        record["note"] = "no OpenAlex record above the similarity floor"
        return record

    authors = _authors_of(best)
    matched_author = next((a for a in authors if names_match(candidate_name, a)), None)
    oa_year = best.get("publication_year")
    year_ok = (cv_year is None or oa_year is None
               or abs(int(cv_year) - int(oa_year)) <= schema.YEAR_TOLERANCE)

    record.update({
        "openalex_id": best.get("id"),
        "matched_title": best.get("display_name"),
        "title_similarity": best_sim,
        "year_openalex": oa_year,
        "authors_openalex": authors,
        "author_match": matched_author,
        "cited_by_count": best.get("cited_by_count"),
        "doi": best.get("doi"),
    })

    exact = best_sim >= schema.TITLE_SIMILARITY_EXACT

    if matched_author and exact and year_ok:
        record["outcome"] = "matched"
        record["note"] = (f"title and author both consistent with this candidate. "
                          f"NOT proof of authorship - a name match is not an "
                          f"identity match.")
    elif matched_author:
        record["outcome"] = "weak_match"
        why = [] if exact else [f"title similarity {best_sim}"]
        if not year_ok:
            why.append(f"year {cv_year} vs indexed {oa_year}")
        record["note"] = "author consistent, but " + " and ".join(why)
    elif exact:
        record["outcome"] = "unconfirmed_author"
        record["note"] = ("this exact paper is indexed, but the candidate's name "
                          "was not found among its authors. Could not confirm "
                          "authorship - name variants and transliteration differ, "
                          "so this is not a finding of non-authorship.")
    else:
        record["outcome"] = "not_found"
        record["note"] = (f"closest match scored {best_sim} with no author "
                          f"agreement - most likely a different paper")

    return record


# ---------------------------------------------------------------------------
# Candidate-level driver
# ---------------------------------------------------------------------------

def verify_candidate(validated: dict, *, use_cache: bool = True) -> dict:
    """Verify every publication in a Stage 4 validated record."""
    profile = validated.get("profile", {})
    name_field = profile.get("full_name") or {}
    candidate_name = name_field.get("value") if isinstance(name_field, dict) else None

    results = []
    for index, pub in enumerate(validated.get("publications", [])):
        validation = pub.get("validation", {}) or {}

        # Entries excluded from credit are still looked up (the reviewer may
        # want the link) but the exclusion is recorded alongside, so Stage 6
        # never has to re-derive it.
        exclusions = []
        if any("thesis" in f or "dissertation" in f for f in validation.get("flags", [])):
            exclusions.append("thesis_or_dissertation")
        if validation.get("verdict") == "unverified":
            exclusions.append("failed_validation")

        verification = verify_publication(pub, candidate_name or "", use_cache=use_cache)
        results.append({
            "index": index,
            "title": pub.get("title"),
            "status": pub.get("status"),
            "credit_exclusions": exclusions,
            "verification": verification,
        })

    return {
        "candidate_id": validated.get("candidate_id"),
        "candidate_name": candidate_name,
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "publications": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    use_cache = "--no-cache" not in argv
    paths = [a for a in argv if not a.startswith("--")]

    out_dir = Path(__file__).resolve().parent.parent / "output"
    files = ([Path(p) for p in paths] if paths
             else sorted(out_dir.glob("Candidate_*.validated.json")))
    if not files:
        print(f"No *.validated.json in {out_dir}. Run validate.py first.")
        return 1

    print(f"Stage 5: OpenAlex verification  (cache "
          f"{'on' if use_cache else 'OFF'}, exact>={schema.TITLE_SIMILARITY_EXACT}, "
          f"close>={schema.TITLE_SIMILARITY_CLOSE})\n")

    for path in files:
        stem = path.name.replace(".validated.json", "")
        validated = json.loads(path.read_text(encoding="utf-8"))
        result = verify_candidate(validated, use_cache=use_cache)

        (out_dir / f"{stem}.verified.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        tally: dict[str, int] = {}
        for entry in result["publications"]:
            key = entry["verification"]["outcome"]
            tally[key] = tally.get(key, 0) + 1

        print(f"{stem}  ({result['candidate_name']})  "
              f"{len(result['publications'])} publication(s)")
        print(f"  outcomes: {tally or 'none'}")
        for entry in result["publications"]:
            v = entry["verification"]
            flag = (" [EXCLUDED: " + ",".join(entry["credit_exclusions"]) + "]"
                    if entry["credit_exclusions"] else "")
            print(f"    [{entry['index']:>2}] {v['outcome']:<19} "
                  f"sim={v['title_similarity']:<5} "
                  f"author={'yes' if v['author_match'] else 'no ':<3} "
                  f"{str(entry['title'])[:40]}{flag}")
        print()

    print(f"Wrote *.verified.json to {out_dir}")
    print(f"Cached responses in {CACHE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
