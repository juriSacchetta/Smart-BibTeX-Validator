"""Deduplication of BibTeX entries using DOI as primary key, with conservative fallback"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from rapidfuzz.fuzz import token_set_ratio

from .normalize import normalize_doi, normalize_title_for_query, normalize_year
from .validator import authors_to_list, extract_last_name


@dataclass(frozen=True)
class DedupeDecision:
    """Record of a deduplication decision for reportability"""
    keep_id: str
    drop_ids: List[str]
    reason: str  # "doi" | "title+year+author" | "title-only"
    confidence: float  # 0..1


def _first_author_last(entry: Dict) -> str:
    """Extract last name of first author, normalized to lowercase"""
    authors = authors_to_list(entry.get("author"))
    if not authors:
        return ""
    return extract_last_name(authors[0]) or ""


def _norm_title(entry: Dict) -> str:
    """Normalize title by removing LaTeX/braces and lowercasing for dedupe key"""
    return normalize_title_for_query((entry.get("title") or "").strip()).lower()


def dedupe_fingerprint(entry: Dict) -> Tuple[Optional[str], Optional[str]]:
    """
    Compute deduplication fingerprints for an entry.
    
    Returns (doi_key, fallback_key):
      - doi_key: normalized DOI (lowercase) or None
      - fallback_key: "title|year|firstauthor" when enough info exists, else None
    """
    doi_key = normalize_doi((entry.get("doi") or "").strip()) if entry.get("doi") else None

    title = _norm_title(entry)
    year = normalize_year(entry.get("year") or "")
    fa = _first_author_last(entry)

    fallback_key = None
    if title and year and fa:
        fallback_key = f"{title}|{year}|{fa}"
    
    return doi_key, fallback_key


def find_duplicates(
    entries: List[Dict],
    title_threshold: float = 0.95,
    allow_title_only: bool = False,
) -> Tuple[List[DedupeDecision], Dict[str, List[str]]]:
    """
    Detect duplicate entries using conservative matching policy.
    
    Returns:
      - decisions: list of DedupeDecision (keep_id + drop_ids)
      - groups: {keep_id: [drop_ids...]} convenience map for applying deduplication
    
    Policy:
      1) If DOI matches => duplicates (confidence=1.0)
      2) Else match on fallback_key (title+year+first-author) AND verify title similarity >= threshold
      3) Optionally allow title-only grouping (off by default, risky)
    
    Args:
        entries: List of BibTeX entry dicts
        title_threshold: Title similarity threshold for fallback matching (default: 0.95)
        allow_title_only: If True, also group by title alone when DOI/year/author missing (default: False)
    """
    by_doi: Dict[str, List[Dict]] = {}
    by_fallback: Dict[str, List[Dict]] = {}
    by_title: Dict[str, List[Dict]] = {}

    # Cluster entries by fingerprints
    for e in entries:
        eid = e.get("ID")
        if not eid:
            continue

        doi_key, fallback_key = dedupe_fingerprint(e)
        
        if doi_key:
            by_doi.setdefault(doi_key, []).append(e)
        
        if fallback_key:
            by_fallback.setdefault(fallback_key, []).append(e)
        
        if allow_title_only:
            t = _norm_title(e)
            if t:
                by_title.setdefault(t, []).append(e)

    decisions: List[DedupeDecision] = []
    groups: Dict[str, List[str]] = {}

    def _choose_keep(cluster: List[Dict]) -> Dict:
        """Deterministic: keep first in file order"""
        return cluster[0]

    def _title_sim(a: Dict, b: Dict) -> float:
        """Compute title similarity between two entries"""
        at = (a.get("title") or "").strip().lower()
        bt = (b.get("title") or "").strip().lower()
        if not at or not bt:
            return 0.0
        return token_set_ratio(at, bt) / 100.0

    # 1) DOI-based clustering (most trustworthy)
    for doi, cluster in by_doi.items():
        if len(cluster) < 2:
            continue
        
        keep = _choose_keep(cluster)
        drops = [e["ID"] for e in cluster if e.get("ID") and e["ID"] != keep["ID"]]
        
        if drops:
            decisions.append(DedupeDecision(
                keep_id=keep["ID"],
                drop_ids=drops,
                reason="doi",
                confidence=1.0
            ))
            groups[keep["ID"]] = drops

    # Build a set of IDs already handled by DOI decisions
    handled = {d.keep_id for d in decisions}
    for d in decisions:
        handled.update(d.drop_ids)

    # 2) Fallback key clustering (title+year+first-author)
    for fk, cluster in by_fallback.items():
        # Ignore entries already handled by DOI decisions
        cluster2 = [e for e in cluster if e.get("ID") and e["ID"] not in handled]
        if len(cluster2) < 2:
            continue

        keep = _choose_keep(cluster2)
        drops = []
        
        for e in cluster2[1:]:
            sim = _title_sim(keep, e)
            if sim >= title_threshold:
                drops.append(e["ID"])
        
        if drops:
            decisions.append(DedupeDecision(
                keep_id=keep["ID"],
                drop_ids=drops,
                reason="title+year+author",
                confidence=title_threshold,
            ))
            groups.setdefault(keep["ID"], []).extend(drops)
            handled.add(keep["ID"])
            handled.update(drops)

    # 3) Optional title-only clustering (very conservative; off by default)
    if allow_title_only:
        for t, cluster in by_title.items():
            # Ignore entries already handled
            cluster2 = [e for e in cluster if e.get("ID") and e["ID"] not in handled]
            if len(cluster2) < 2:
                continue
            
            keep = _choose_keep(cluster2)
            drops = []
            
            for e in cluster2[1:]:
                sim = _title_sim(keep, e)
                if sim >= title_threshold:
                    drops.append(e["ID"])
            
            if drops:
                decisions.append(DedupeDecision(
                    keep_id=keep["ID"],
                    drop_ids=drops,
                    reason="title-only",
                    confidence=title_threshold,
                ))
                groups.setdefault(keep["ID"], []).extend(drops)
                handled.add(keep["ID"])
                handled.update(drops)

    return decisions, groups


def apply_dedupe_drop_only(entries: List[Dict], groups: Dict[str, List[str]]) -> List[Dict]:
    """
    Apply deduplication by dropping duplicate entries (keep-first policy).
    
    NOTE: This only removes duplicates; it does NOT merge fields or rewrite citekeys.
    Keeps all entries whose IDs are not in the drop list.
    
    Args:
        entries: Original list of BibTeX entries
        groups: {keep_id: [drop_ids...]} mapping from dedupe decisions
    
    Returns:
        Filtered list of entries (dropped duplicates removed)
    """
    drop_ids = set()
    for keep_id, drops in groups.items():
        drop_ids.update(drops)

    out = []
    for e in entries:
        eid = e.get("ID")
        if eid and eid in drop_ids:
            continue
        out.append(e)
    
    return out
