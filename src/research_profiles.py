"""
Research profiles a CV links to: ORCID, Google Scholar, Scopus.

WHAT IS RETRIEVED, AND WHY ONLY THAT
  ORCID   An ORCID iD is a registry identifier with a check digit. OpenAlex,
          the open index Stage 5 already uses, resolves it to an author
          record (works, citations, h-index) and lists the works attributed
          to it. Both are free, documented, unauthenticated APIs, so the
          lookup is reproducible and every figure carries its query URL.
  Scholar Google Scholar has no public API and its terms forbid automated
          retrieval. The link is recorded and shown; its figures are never
          fetched and never scored.
  Scopus  The Scopus API needs an institutional key. The ID is recorded and
          shown for manual checking.

  So a figure the CV reports from Scholar or Scopus ("h-index 13") is
  shown beside the verified one and, when it is higher, flagged for the
  reviewer - never credited on the CV's word alone.

IDENTITY BEFORE ANYTHING ELSE
  An ORCID iD can be mistyped into someone else's. The author record's name
  must match the candidate's before any of its figures are used; a
  mismatch is reported and nothing from that record is scored.

WORKS IN THE PROFILE THAT THE CV DOES NOT LIST
  Many CVs print "selected" or "representative" publications and state a
  larger total ("20+ peer-reviewed journal publications"). Scoring only
  the printed handful under-scores exactly the candidates with the most
  output. When the CV SAYS its list is partial, works attributed to the
  verified ORCID record that the CV does not list are credited, up to the
  total the CV claims, each with its OpenAlex ID and DOI. When the CV
  presents its list as complete, profile-only works are shown and not
  credited: an index can attribute a stranger's paper to an author record,
  and the CV is the candidate's own account of their work.

  Only peer-reviewed forms count (journal articles, reviews, conference
  papers, book chapters). Preprints, datasets, theses and repository
  copies do not. Every profile work is checked against the CV's entries
  by OpenAlex ID, DOI and title, so nothing is counted twice.

NO RULE HERE NAMES A CANDIDATE.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlencode

ORCID_ID = re.compile(r"(?<![\d-])(\d{4}-\d{4}-\d{4}-\d{3}[\dX])(?![\d-])")
SCHOLAR_USER = re.compile(
    r"scholar\.google\.[a-z.]+/citations\?\S*?user=([A-Za-z0-9_-]{12})", re.I)
SCOPUS_ID = (
    re.compile(r"scopus\.com/authid/detail\.uri\?authorId=(\d{8,12})", re.I),
    re.compile(r"scopus\s*(?:author\s*)?id\s*[:#-]?\s*(\d{8,12})", re.I),
)
H_INDEX = re.compile(r"\bh[\s-]?index\s*(?:of\s*|[:=\-]\s*)?(\d{1,3})\b", re.I)
CITATIONS = re.compile(
    r"\b(\d{1,3}(?:,\d{3})+|\d{1,6})\s*\+?\s*(?:[A-Za-z]+\s+){0,3}"
    r"citations\b"
    r"|\bcitations\s*[:=\-]\s*(\d{1,3}(?:,\d{3})+|\d{1,6})\b", re.I)
PARTIAL_LIST = re.compile(
    r"\b(selected|representative|recent|key|major|sample|some)\s+"
    r"(?:\w+\s+){0,2}(publications|papers|articles)\b"
    r"|\b(full|complete)\s+(?:publication\s+)?list\b", re.I)
CLAIMED_COUNT = re.compile(
    r"\b(\d{1,3})\s*\+?\s*(?:peer[- ]reviewed\s+|refereed\s+)?"
    r"(?:international\s+)?(?:journal\s+)?(publications|papers|articles)\b",
    re.I)

# OpenAlex work types that are peer-reviewed scholarship.
CREDITABLE_WORK_TYPES = {"article", "review", "conference-paper",
                         "proceedings-article", "book-chapter", "letter"}
# Source types that are copies, not venues.
NON_VENUE_SOURCES = {"repository"}

OPENALEX_AUTHORS = "https://api.openalex.org/authors"
WORKS_PAGE = 200

# Credit for a work found only in the verified profile, as a fraction of a
# corroborated publication. It is attributed to the candidate by the
# registry identifier they printed, which is stronger evidence of
# authorship than a name match; the only reason it is not in the CV is
# that the CV said it was showing a selection. DEMO/RESEARCH value.
PROFILE_WORK_CREDIT = 1.0


def orcid_checksum_ok(orcid: str) -> bool:
    """ISO 7064 MOD 11-2, the check digit every ORCID iD carries."""
    digits = orcid.replace("-", "")
    if len(digits) != 16:
        return False
    total = 0
    for ch in digits[:-1]:
        if not ch.isdigit():
            return False
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    return digits[-1] == ("X" if check == 10 else str(check))


def detect_links(source_text: str) -> dict:
    """Profile identifiers and self-reported metrics printed in the CV."""
    text = source_text or ""
    orcids = []
    for match in ORCID_ID.finditer(text):
        orcid = match.group(1)
        if orcid_checksum_ok(orcid) and orcid not in orcids:
            orcids.append(orcid)

    scholar = sorted({m.group(1) for m in SCHOLAR_USER.finditer(text)})
    scopus = sorted({m.group(1) for p in SCOPUS_ID for m in p.finditer(text)})

    h_values = [int(m.group(1)) for m in H_INDEX.finditer(text)]
    citation_values = [int((m.group(1) or m.group(2)).replace(",", ""))
                       for m in CITATIONS.finditer(text)]
    partial = PARTIAL_LIST.search(text)
    counts = [int(m.group(1)) for m in CLAIMED_COUNT.finditer(text)
              if 0 < int(m.group(1)) < 1000]

    return {
        "orcid": orcids,
        "scholar": [{"user": user,
                     "url": f"https://scholar.google.com/citations?user={user}",
                     "retrieval": "not retrieved - Google Scholar has no "
                                  "public API and forbids automated access"}
                    for user in scholar],
        "scopus": [{"id": sid,
                    "url": f"https://www.scopus.com/authid/detail.uri?authorId={sid}",
                    "retrieval": "not retrieved - the Scopus API requires an "
                                 "institutional key"}
                   for sid in scopus],
        "self_reported": {
            "h_index": max(h_values) if h_values else None,
            "citations": max(citation_values) if citation_values else None,
        },
        "partial_list": bool(partial),
        "partial_list_evidence": partial.group(0) if partial else None,
        "claimed_publications": max(counts) if counts else None,
    }


def fetch_orcid_profile(orcid: str, candidate_name: str, *,
                        use_cache: bool = True) -> dict:
    """The OpenAlex author record and works for an ORCID iD, name-checked."""
    import verify_pubs as vp

    author_url = (f"{OPENALEX_AUTHORS}/orcid:{orcid}?"
                  f"{urlencode({'mailto': vp.MAILTO})}")
    works_url = (f"{vp.OPENALEX_WORKS}?" + urlencode({
        "filter": f"author.orcid:{orcid}",
        "per-page": WORKS_PAGE,
        "select": ("id,doi,display_name,type,publication_year,"
                   "cited_by_count,primary_location"),
        "mailto": vp.MAILTO,
    }))
    record = {
        "source": "orcid", "id": orcid,
        "url": f"https://orcid.org/{orcid}",
        "query_urls": [author_url],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "lookup_failed", "works": [],
    }

    author, error = vp.cached_get_json(author_url, use_cache=use_cache,
                                       missing_ok=True)
    if error == "not in OpenAlex":
        record.update(status="not_found",
                      note="OpenAlex has no author record for this ORCID iD")
        return record
    if error:
        record["note"] = (f"OpenAlex could not be reached ({error[:100]}); "
                          f"the profile was not used. This is not held "
                          f"against the candidate.")
        return record

    indexed_names = [author.get("display_name") or ""] + list(
        author.get("display_name_alternatives") or [])
    if not candidate_name or not any(vp.names_match(candidate_name, n)
                                     for n in indexed_names if n):
        record.update(
            status="name_mismatch", display_name=author.get("display_name"),
            note=(f"the ORCID record belongs to {author.get('display_name')!r}, "
                  f"which does not match the candidate's name "
                  f"{candidate_name!r}; nothing from it was used"))
        return record

    stats = author.get("summary_stats") or {}
    record.update(
        status="verified",
        display_name=author.get("display_name"),
        openalex_id=author.get("id"),
        works_count=author.get("works_count"),
        cited_by_count=author.get("cited_by_count"),
        h_index=stats.get("h_index"),
        i10_index=stats.get("i10_index"),
        note=(f"ORCID {orcid} resolves in OpenAlex to "
              f"{author.get('display_name')!r}, matching the candidate"),
    )

    record["query_urls"].append(works_url)
    works, works_error = vp.cached_get_json(works_url, use_cache=use_cache)
    if works_error:
        record["works_note"] = (f"the works list could not be retrieved "
                                f"({works_error[:100]})")
    record["works"] = [{
        "openalex_id": w.get("id"),
        "doi": w.get("doi"),
        "title": w.get("display_name"),
        "year": w.get("publication_year"),
        "cited_by_count": w.get("cited_by_count") or 0,
        **vp.venue_of(w),
    } for w in (works.get("results") or [])]
    return record


def _same_work(work: dict, keys: list[dict]) -> bool:
    import verify_pubs as vp
    doi = vp.normalise_doi(work.get("doi"))
    for key in keys:
        if work.get("openalex_id") and work["openalex_id"] == key.get("openalex_id"):
            return True
        if doi and doi == key.get("doi"):
            return True
        for title in (key.get("title"), key.get("matched_title")):
            if title and vp.title_similarity(work.get("title") or "", title) \
                    >= vp.schema.TITLE_SIMILARITY_EXACT:
                return True
    return False


def profile_works(profile: dict, cv_results: list[dict], links: dict,
                  as_of_year: int | None = None) -> list[dict]:
    """
    The verified profile's works that the CV does not list, each with the
    credit it earns and why. Empty unless the profile is verified.
    """
    if profile.get("status") != "verified":
        return []
    import verify_pubs as vp

    cv_keys = []
    for row in cv_results:
        verification = row.get("verification") or {}
        cv_keys.append({
            "openalex_id": verification.get("openalex_id"),
            "doi": vp.normalise_doi(verification.get("doi")),
            "title": row.get("title"),
            "matched_title": verification.get("matched_title"),
        })

    credited_from_cv = sum(1 for row in cv_results
                           if not row.get("credit_exclusions"))
    claimed = links.get("claimed_publications")
    partial = links.get("partial_list")
    room = (max(0, claimed - credited_from_cv) if claimed else None)

    rows, seen = [], []
    # Most-cited first, so a cap keeps the works with the strongest record.
    ordered = sorted(profile.get("works") or [],
                     key=lambda w: (-(w.get("cited_by_count") or 0),
                                    -(w.get("year") or 0)))
    for work in ordered:
        if _same_work(work, cv_keys):
            continue
        row = dict(work, source="orcid_openalex", orcid=profile.get("id"),
                   credit=0.0)
        if _same_work(work, seen):
            row["reason"] = "a second index record of a work already counted"
            row["duplicate"] = True
            rows.append(row)
            continue
        seen.append({"openalex_id": work.get("openalex_id"),
                     "doi": vp.normalise_doi(work.get("doi")),
                     "title": work.get("title")})

        if work.get("work_type") not in CREDITABLE_WORK_TYPES or \
                work.get("venue_type") in NON_VENUE_SOURCES:
            row["reason"] = (f"a {work.get('work_type') or 'record'} rather "
                             f"than a peer-reviewed publication")
        elif as_of_year and (work.get("year") or 0) > as_of_year:
            row["reason"] = "dated after the as-of date"
        elif not partial:
            row["reason"] = ("in the linked ORCID profile but not listed in "
                             "the CV, which presents its list as complete - "
                             "shown for the reviewer, not credited")
        elif room is not None and room <= 0:
            row["reason"] = (f"beyond the {claimed} publications the CV "
                             f"claims - shown, not credited")
        else:
            row["credit"] = PROFILE_WORK_CREDIT
            row["reason"] = (
                f"attributed to the candidate's verified ORCID record; the "
                f"CV lists a selection ({links.get('partial_list_evidence')!r})"
                + (f" of {claimed} claimed" if claimed else ""))
            if room is not None:
                room -= 1
        rows.append(row)
    return rows
