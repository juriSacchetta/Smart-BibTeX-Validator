"""BibTeX lint checks - local quality validation without network"""

import re
from typing import Dict, List, Tuple
from .normalize import normalize_doi


REQUIRED_FIELDS_BY_TYPE = {
    "article": ["title", "author", "year", "journal"],
    "inproceedings": ["title", "author", "year", "booktitle"],
    "incollection": ["title", "author", "year", "booktitle"],
    "book": ["title", "author", "year", "publisher"],
    "phdthesis": ["title", "author", "year", "school"],
    "mastersthesis": ["title", "author", "year", "school"],
    "online": ["title", "url"],
    "misc": ["title"],
}


def is_web_resource(entry: Dict) -> bool:
    """Check if entry is a web resource (online, misc, manual, etc.)"""
    entry_type = (entry.get("ENTRYTYPE") or "").lower()
    return entry_type in ("online", "misc", "manual")


def lint_entry(entry: Dict) -> List[str]:
    """
    Return a list of lint issues for one entry.
    Issues are plain strings; caller can prefix with LINT: or other prefix.
    
    Returns issues with prefixes:
      - LINT:BLOCKING: for issues that prevent validation (missing required fields, format errors)
      - LINT:WEB_CITE: for web-specific suggestions (missing urldate/access date)
      - LINT: for other general issues
    """
    issues: List[str] = []

    entry_id = entry.get("ID", "unknown")
    entry_type = (entry.get("ENTRYTYPE") or "").lower()
    is_web = is_web_resource(entry)

    # Basic required fields
    required = REQUIRED_FIELDS_BY_TYPE.get(entry_type, ["title", "year"])
    for f in required:
        if not entry.get(f):
            issues.append(f"BLOCKING:MISSING_FIELD:{f}")

    # Special handling for @misc: allow url OR howpublished
    if entry_type == "misc":
        if not entry.get("url") and not entry.get("howpublished"):
            issues.append("BLOCKING:MISSING_FIELD:url_or_howpublished")

    # Author formatting sanity (only for academic types)
    if not is_web:
        author = entry.get("author")
        if author:
            if isinstance(author, str):
                if " and " not in author and "," not in author and len(author.split()) < 2:
                    issues.append("BLOCKING:AUTHOR_FORMAT:suspicious_author_string")
            else:
                issues.append("BLOCKING:AUTHOR_FORMAT:author_not_string")

    # Year sanity (skip for web resources)
    if not is_web:
        year = str(entry.get("year") or "").strip()
        if year and not re.fullmatch(r"\d{4}", year):
            issues.append(f"BLOCKING:YEAR_FORMAT:{year}")

    # DOI sanity (if present) - use normalize_doi for consistent validation
    raw_doi = str(entry.get("doi") or "").strip()
    if raw_doi:
        normalized = normalize_doi(raw_doi)
        if not normalized:
            issues.append(f"BLOCKING:DOI_FORMAT:{raw_doi}")

    # URL sanity (if present)
    url = str(entry.get("url") or "").strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        issues.append(f"BLOCKING:URL_FORMAT:{url}")

    # Duplicate venue fields that often cause confusion (only for academic types)
    if not is_web:
        if entry.get("journal") and entry.get("booktitle"):
            issues.append("BLOCKING:VENUE_FIELDS:both_journal_and_booktitle")

    # Warn about very low-signal titles
    title = str(entry.get("title") or "").strip()
    if title and len(title) < 6:
        issues.append("BLOCKING:TITLE_QUALITY:very_short_title")

    # Web-specific checks: access date recommendation
    if is_web and url:
        has_urldate = entry.get("urldate")
        has_accessed_note = "accessed" in (entry.get("note") or "").lower()
        
        if not has_urldate and not has_accessed_note:
            issues.append("WEB_CITE:MISSING_ACCESS_DATE")

    return issues


def lint_entries(entries: List[Dict]) -> Tuple[Dict[str, List[str]], int]:
    """
    Returns:
      - per_entry_issues: {entry_id: [issues...]}
      - total_issue_count (counts all issues, including non-blocking)
    """
    per_entry: Dict[str, List[str]] = {}
    total = 0
    for e in entries:
        eid = e.get("ID", "unknown")
        issues = lint_entry(e)
        if issues:
            per_entry[eid] = issues
            total += len(issues)
    return per_entry, total


def has_blocking_lint_issues(lint_issues: List[str]) -> bool:
    """Check if lint issues list contains any blocking issues"""
    return any(issue.startswith("BLOCKING:") for issue in lint_issues)
