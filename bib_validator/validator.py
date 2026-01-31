"""Core validation logic"""

import logging
import time
import requests
import difflib
from typing import Dict, List, Optional, Any
from copy import deepcopy
from rapidfuzz.fuzz import token_set_ratio
from .sources import ValidationSource, build_sources, DEFAULT_ORDER
from .lint import lint_entries, is_web_resource, has_blocking_lint_issues
from .normalize import normalize_doi, normalize_title_for_query, normalize_author_string
from .url_check import check_url, is_doi_url
from .interactive_cli import choose_candidate_interactive


logger = logging.getLogger(__name__)

# Title similarity threshold for matching (0.0 to 1.0)
# Used in final comparison reporting to flag low-confidence matches.
TITLE_SIMILARITY_THRESHOLD = 0.90

# Hard gate: do not accept a source "match" unless it meets these minima.
# (Prevents catastrophic wrong-paper substitutions.)
# MIN_MATCH_TITLE_SIMILARITY: During candidate acceptance (safety gate), reject if similarity falls below this.
#   Example: if we query for "Machine Learning" and get back "Deep Learning", this catches the mismatch early.
# MIN_MATCH_AUTHOR_OVERLAP: Reject title-based matches if author last-name sets overlap too little.
#   Example: Smith, Jones vs. Brown, Davis -> 0% overlap -> reject, likely different paper.
MIN_MATCH_TITLE_SIMILARITY = 0.88
MIN_MATCH_AUTHOR_OVERLAP = 0.60

# Multi-candidate selection policy
MAX_CANDIDATES_PER_SOURCE = 5
AUTO_ACCEPT_SCORE = 0.90
MIN_SCORE_GAP = 0.08


def authors_to_list(authors_val: Any) -> List[str]:
    """Convert author value (string, list, or object) to list of strings"""
    if not authors_val:
        return []
    if isinstance(authors_val, list):
        return [str(a).strip() for a in authors_val if str(a).strip()]

    # Normalize author string first to handle comma-separated and "and"-separated formats
    author_str = normalize_author_string(str(authors_val))
    return [a.strip() for a in author_str.split(" and ") if a.strip()]


def extract_last_name(author_str: str) -> str:
    """
    Extract last name from an author string.
    Handles formats like "Smith, J.", "John Smith", "J. Smith", etc.
    Returns lowercase normalized last name.
    Normalizes to reduce false mismatches from BibTeX/LaTeX formatting.
    """
    if not author_str:
        return ""

    # Normalize to reduce false mismatches from BibTeX/LaTeX formatting.
    # This affects reporting only; safety gate is handled separately.
    author_str = normalize_author_string(author_str)
    author_str = author_str.replace("{", "").replace("}", "")
    author_str = author_str.strip()

    # If contains comma, last name is before comma (e.g., "Smith, J.")
    if "," in author_str:
        last_name = author_str.split(",", 1)[0].strip()
    else:
        # Otherwise, last name is last word (e.g., "John Smith" or "J. Smith")
        parts = author_str.split()
        last_name = parts[-1] if parts else ""

    # Remove trailing periods and normalize
    last_name = last_name.rstrip(".")

    return last_name.lower()


def compare_author_sets(
    orig_authors: List[str], corr_authors: List[str]
) -> Optional[str]:
    """
    Compare two author lists by last name set overlap.
    Returns issue string if mismatch detected, else None.
    Uses a less aggressive threshold for reporting (0.5) than the safety gate (0.6).
    """
    if not orig_authors or not corr_authors:
        return None

    # Extract last names (normalized to lowercase)
    orig_last_names = {extract_last_name(a) for a in orig_authors if a}
    corr_last_names = {extract_last_name(a) for a in corr_authors if a}

    # If either set is empty after normalization, skip detailed check
    if not orig_last_names or not corr_last_names:
        return None

    # Compute overlap
    overlap = orig_last_names & corr_last_names
    overlap_ratio = len(overlap) / max(len(orig_last_names), len(corr_last_names))

    # Reporting threshold (less strict than the acceptance gate):
    # Many sources differ in author formatting or truncate lists.
    if overlap_ratio < 0.5:
        orig_str = ", ".join(sorted(orig_last_names))
        corr_str = ", ".join(sorted(corr_last_names))
        return f"AUTHORS: Low last-name overlap ({overlap_ratio:.1%}). Original: {orig_str} vs Corrected: {corr_str}"

    return None


class SmartBibtexValidator:
    """Validates BibTeX entries against multiple academic sources"""

    def __init__(
        self,
        entries: List[Dict],
        sources: Optional[Dict[str, ValidationSource]] = None,
        source_delay: float = 0.1,
        entry_delay: float = 0.0,
        max_workers: int = 3,
        entry_timeout: float = 15.0,
        stop_on_first_match: bool = True,
        interactive_disambiguation: bool = False,
    ):
        """
        Initialize validator

        Args:
            entries: List of BibTeX entries to validate
            sources: Dict of source_name -> ValidationSource. If None, all defaults used.
            source_delay: Delay (seconds) between source queries within an entry
            entry_delay: Delay (seconds) between entries
            max_workers: Max parallel threads per entry (sources queried concurrently)
            entry_timeout: Timeout (seconds) for all source queries on a single entry
            stop_on_first_match: If True, stop querying sources after first match (preferred order)
            interactive_disambiguation: If True, prompt user to select among ambiguous matches
        """
        self.entries = entries
        self.sources = sources or build_sources(DEFAULT_ORDER)
        self.source_delay = source_delay
        self.entry_delay = entry_delay
        self.max_workers = max_workers
        self.entry_timeout = entry_timeout
        self.stop_on_first_match = stop_on_first_match
        self.interactive_disambiguation = interactive_disambiguation
        self.results = {
            "validated": [],
            "mismatches": [],
            "not_found": [],
        }
        self.lint_results = {}
        self.lint_issue_count = 0
        self.lint_entry_count = 0
        # Per-source caches: {source_name: {"doi": {...}, "title": {...}}}
        # Title cache now stores lists of candidates
        self.source_caches = {name: {"doi": {}, "title": {}} for name in self.sources}
        # URL reachability check cache: {url: (ok: bool, detail: str)}
        self.url_check_cache: Dict[str, tuple] = {}
        # Session for URL checking (reuses connection)
        self.url_check_session = requests.Session()

        logger.debug(
            "Validator initialized: entries=%d sources=%s source_delay=%.3f entry_delay=%.3f max_workers=%d entry_timeout=%.3f stop_on_first_match=%s interactive=%s",
            len(self.entries),
            list(self.sources.keys()),
            self.source_delay,
            self.entry_delay,
            self.max_workers,
            self.entry_timeout,
            self.stop_on_first_match,
            self.interactive_disambiguation,
        )

    def validate_all(self):
        """Validate all entries against sources"""
        # Validation pipeline phases:
        #  1) Lint stage (local, no network): detect missing/invalid BibTeX fields.
        #  2) Per-entry remote validation: query academic sources (Crossref/DBLP/S2).
        #     - Optional URL reachability check (best-effort).
        #     - Web resources are explicitly excluded from remote matching.
        #  3) Compare local entry vs. chosen remote candidate -> issues / status:
        #     - "validated": remote match found and no mismatch issues
        #     - "mismatch": remote match found but disagrees on key fields
        #     - "not_found": no acceptable remote match (including if candidates were rejected)
        if not self.entries:
            print("⚠ No entries to validate")
            return

        # Lint stage (local, no network)
        self.lint_results, self.lint_issue_count = lint_entries(self.entries)
        self.lint_entry_count = len(self.lint_results)
        print(
            f"🧹 Lint stage: found {self.lint_issue_count} issues across "
            f"{self.lint_entry_count} entries with issues (out of {len(self.entries)} total)\n"
        )

        print("🔍 Starting validation against multiple sources...")
        if self.stop_on_first_match:
            print("📊 Using preferred-order matching with multi-candidate ranking")
        else:
            print(f"📊 Using {self.max_workers} parallel source queries per entry")

        total = len(self.entries)
        for idx, entry in enumerate(self.entries, 1):
            entry_id = entry.get("ID", "unknown")
            title = entry.get("title", "No title")[:50]

            t0 = time.time()
            logger.debug(
                "Validating entry id=%s title=%r", entry_id, entry.get("title", "")
            )

            print(f"[{idx}/{total}] {entry_id}: {title}...", end=" ")

            result = self.validate_entry(entry)

            logger.debug(
                "Finished entry id=%s status=%s matches=%s elapsed=%.3fs",
                entry_id,
                result.get("status"),
                list(result.get("matches", {}).keys()),
                time.time() - t0,
            )

            if result["status"] == "validated":
                print("✓")
                self.results["validated"].append(result)
            elif result["status"] == "mismatch":
                print("⚠")
                for issue in result["issues"][:2]:  # Show first 2 issues inline
                    print(f"    {issue}")
                if len(result["issues"]) > 2:
                    print(f"    ... and {len(result['issues']) - 2} more issues")
                self.results["mismatches"].append(result)
            elif result["status"] == "not_found":
                print("✗")
                self.results["not_found"].append(result)

            # Delay between entries (much smaller now)
            if self.entry_delay > 0:
                time.sleep(self.entry_delay)

    def validate_entry(self, entry: Dict) -> Dict:
        """Validate single entry against sources"""
        # Per-entry result schema (consumed by console output, reporting, and correction application):
        #  - matches: accepted source matches keyed by source name
        #  - corrected_fields: fields extracted from the chosen preferred match (if any)
        #  - issues: combined lint + remote comparison issues (strings, often prefixed with SOURCE:)
        #  - needs_review/review_reasons: human attention flags; not necessarily fatal
        #  - match_accepted: True only if a remote candidate passed the safety gate during validation.
        #  - rejected_candidates: candidate matches that were found but rejected as unsafe
        #  - candidate_matches: ranked shortlist when ambiguous (no auto-accept)
        result = {
            "id": entry.get("ID", "unknown"),
            "title": entry.get("title", ""),
            "status": "unknown",
            "issues": [],
            "corrected_fields": {},
            "search_method": None,
            "matches": {},
            "attempts": {},
            "autofilled_fields": [],
            "match_kind": None,
            "match_confidence": None,
            "needs_review": False,
            "review_reasons": [],
            "match_accepted": False,
            "rejected_candidates": [],
            "candidate_matches": [],
        }

        # Inject lint issues first
        lint_issues = self.lint_results.get(result["id"], [])
        if lint_issues:
            # Format lint issues for report: prefix with LINT:
            result["issues"].extend([f"LINT:{i}" for i in lint_issues])

        # Check URL reachability (if entry has a URL)
        self._maybe_check_url(entry, result)

        # Check if this is a web resource
        entry_type = (entry.get("ENTRYTYPE") or "").lower()
        is_web = is_web_resource(entry)

        # PHASE: Web-resource bypass
        # Web entries (e.g., @misc with URL) are not reliably represented in Crossref/DBLP/S2,
        # so attempting remote matching causes false mismatches. For these:
        #  - skip remote validation entirely
        #  - rely on lint + URL reachability to decide needs_review
        if is_web:
            logger.debug(
                "Entry id=%s is web resource; skipping remote validation", result["id"]
            )
            result["issues"].append("REMOTE: skipped (web resource)")

            # Web resources validated if no blocking lint issues
            blocking_lint = has_blocking_lint_issues(lint_issues)
            if blocking_lint:
                result["status"] = "not_found"
                result["needs_review"] = True
                result["review_reasons"].append("WEB: blocking lint issues")
            else:
                result["status"] = "validated"

            return result

        # PHASE: Build source query plan
        # Each source can decide locally if it is applicable (e.g., needs DOI/title).
        # We record "attempts" for reporting transparency even when a source is skipped.
        # tasks contains only sources we will actually query for this entry.
        # Collect tasks: (source_name, source)
        tasks = []
        for source_name in DEFAULT_ORDER:
            if source_name not in self.sources:
                continue

            source = self.sources[source_name]
            can_try, why = source.should_attempt(entry)
            result["attempts"][source_name] = {
                "attempted": bool(can_try),
                "reason": why,
            }

            if not can_try:
                logger.debug(
                    "Source %s skipping entry id=%s (%s)",
                    source_name,
                    result["id"],
                    why,
                )
            else:
                tasks.append((source_name, source))

        # PHASE: Remote query
        # Two modes:
        #  - stop_on_first_match=True: sequential in preferred order, gather candidates, rank globally
        #  - stop_on_first_match=False: parallel (legacy); gather multiple candidates then prefer by DEFAULT_ORDER
        #
        # IMPORTANT: even if a source returns "found", we do NOT automatically accept it.
        # We run a safety gate (_accept_match) to prevent catastrophic wrong-paper substitutions
        # (e.g., low title similarity and incompatible authors).
        # Then we score each candidate and auto-accept only if unambiguous (high score + clear gap).
        timed_out_sources = set()

        if self.stop_on_first_match:
            # Sequential candidate gathering with global ranking
            all_candidates: List[Dict] = []
            for source_name, source in tasks:
                try:
                    cand_tuples = self._query_source(source_name, source, entry)  # List[(found, fields, method)]
                    if not cand_tuples:
                        continue

                    for found, corrected_fields, search_method in cand_tuples:
                        accepted, reject_reasons, title_sim_gate = self._accept_match(
                            entry, corrected_fields, search_method
                        )
                        if not accepted:
                            result["rejected_candidates"].append(
                                {
                                    "source": source_name,
                                    "search_method": search_method,
                                    "reasons": reject_reasons,
                                    "title_similarity": title_sim_gate,
                                }
                            )
                            logger.debug(
                                "Rejected candidate match: entry_id=%s source=%s method=%s reasons=%s",
                                result["id"],
                                source_name,
                                search_method,
                                reject_reasons,
                            )
                            continue

                        scored = self._score_candidate(entry, corrected_fields)
                        if scored["hard_reject_reasons"]:
                            result["rejected_candidates"].append(
                                {
                                    "source": source_name,
                                    "search_method": search_method,
                                    "reasons": scored["hard_reject_reasons"],
                                    "title_similarity": scored["components"]["title"],
                                }
                            )
                            logger.debug(
                                "Hard-rejected candidate: entry_id=%s source=%s reasons=%s",
                                result["id"],
                                source_name,
                                scored["hard_reject_reasons"],
                            )
                            continue

                        all_candidates.append(
                            {
                                "source": source_name,
                                "search_method": search_method,
                                "source_data": found,
                                "corrected_fields": corrected_fields,
                                "score": scored["score"],
                                "components": scored["components"],
                            }
                        )
                except Exception as e:
                    logger.debug(
                        "Source %s failed for entry id=%s: %s",
                        source_name,
                        result["id"],
                        e,
                        exc_info=True,
                    )
                    result["attempts"][source_name]["error"] = str(e)

            # Choose the best candidate, but only auto-accept if unambiguous
            if all_candidates:
                all_candidates.sort(key=lambda x: x["score"], reverse=True)
                
                # If any DOI-driven candidate exists, select it deterministically.
                # DOI matches should never require interactive disambiguation.
                doi_candidate = next(
                    (c for c in all_candidates if (c.get("search_method") or "").endswith(":DOI")),
                    None,
                )
                if doi_candidate is not None:
                    src = doi_candidate["source"]
                    result["matches"][src] = {
                        "search_method": doi_candidate["search_method"],
                        "source_data": doi_candidate["source_data"],
                        "corrected_fields": doi_candidate["corrected_fields"],
                    }
                    result["corrected_fields"] = doi_candidate["corrected_fields"]
                    result["search_method"] = doi_candidate["search_method"]
                    result["match_accepted"] = True
                    result["match_kind"] = "doi"
                    result["match_confidence"] = 1.0
                    # Not ambiguous anymore
                    result["candidate_matches"] = []
                    logger.debug(
                        "Auto-accepted DOI candidate: entry_id=%s source=%s",
                        result["id"],
                        src,
                    )
                else:
                    best = all_candidates[0]
                    second = all_candidates[1] if len(all_candidates) > 1 else None
                    gap = (best["score"] - second["score"]) if second else 1.0

                    if best["score"] >= AUTO_ACCEPT_SCORE and gap >= MIN_SCORE_GAP:
                        # Auto-accept: high confidence and clear winner
                        src = best["source"]
                        result["matches"][src] = {
                            "search_method": best["search_method"],
                            "source_data": best["source_data"],
                            "corrected_fields": best["corrected_fields"],
                        }
                        result["corrected_fields"] = best["corrected_fields"]
                        result["search_method"] = best["search_method"]
                        result["match_accepted"] = True
                        result["match_kind"] = "doi" if best["search_method"].endswith(":DOI") else "composite"
                        result["match_confidence"] = best["score"]

                        logger.debug(
                            "Auto-accepted candidate: entry_id=%s source=%s score=%.2f gap=%.2f",
                            result["id"],
                            src,
                            best["score"],
                            gap,
                        )
                    else:
                        # Ambiguous: keep shortlist for human review or interactive selection
                        result["candidate_matches"] = all_candidates[:5]
                        
                        # If interactive mode enabled, prompt user to select
                        if self.interactive_disambiguation:
                            selected_idx = choose_candidate_interactive(
                                entry, result["candidate_matches"], entry_id=result["id"]
                            )
                            if selected_idx is not None:
                                selected = result["candidate_matches"][selected_idx]
                                src = selected["source"]
                                result["matches"][src] = {
                                    "search_method": selected["search_method"],
                                    "source_data": selected["source_data"],
                                    "corrected_fields": selected["corrected_fields"],
                                }
                                result["corrected_fields"] = selected["corrected_fields"]
                                result["search_method"] = selected["search_method"]
                                result["match_accepted"] = True
                                result["match_kind"] = (
                                    "doi" if selected["search_method"].endswith(":DOI") else "composite"
                                )
                                result["match_confidence"] = selected["score"]
                                logger.debug(
                                    "User selected candidate: entry_id=%s source=%s score=%.2f",
                                    result["id"],
                                    src,
                                    selected["score"],
                                )
                        else:
                            # Mark as needing review if not interactive
                            result["needs_review"] = True
                            result["review_reasons"].append(
                                f"AMBIGUOUS_MATCH: best_score={best['score']:.2f}, gap={gap:.2f}"
                            )
                            logger.debug(
                                "Ambiguous match (needs review): entry_id=%s best_score=%.2f gap=%.2f candidates=%d",
                                result["id"],
                                best["score"],
                                gap,
                                len(all_candidates),
                            )
        else:
            # Legacy parallel mode (kept for backwards compatibility)
            from concurrent.futures import (
                ThreadPoolExecutor,
                as_completed,
                TimeoutError as ConcurrentTimeoutError,
            )

            executor = ThreadPoolExecutor(max_workers=self.max_workers)
            try:
                # Submit all tasks
                future_to_source = {
                    executor.submit(
                        self._query_source, source_name, source, entry
                    ): source_name
                    for source_name, source in tasks
                }

                # Collect results as they complete with timeout
                wait_start = time.time()
                try:
                    for future in as_completed(
                        future_to_source, timeout=self.entry_timeout
                    ):
                        source_name = future_to_source[future]
                        try:
                            cand_tuples = future.result()
                            if not cand_tuples:
                                continue

                            for found, corrected_fields, search_method in cand_tuples:
                                # SAFETY GATE (parallel mode): accept into matches only if safe.
                                accepted, reject_reasons, title_sim = self._accept_match(
                                    entry, corrected_fields, search_method
                                )
                                if accepted:
                                    result["matches"][source_name] = {
                                        "search_method": search_method,
                                        "source_data": found,
                                        "corrected_fields": corrected_fields,
                                    }
                                else:
                                    result["rejected_candidates"].append(
                                        {
                                            "source": source_name,
                                            "search_method": search_method,
                                            "reasons": reject_reasons,
                                            "title_similarity": title_sim,
                                        }
                                    )
                                    logger.debug(
                                        "Rejected candidate match: entry_id=%s source=%s method=%s reasons=%s",
                                        result["id"],
                                        source_name,
                                        search_method,
                                        reject_reasons,
                                    )
                        except Exception as e:
                            # Log but don't crash on individual source failures
                            logger.debug(
                                "Source %s failed for entry id=%s: %s",
                                source_name,
                                result["id"],
                                e,
                                exc_info=True,
                            )
                            result["attempts"][source_name]["error"] = str(e)
                except ConcurrentTimeoutError:
                    # Some sources didn't finish in time; record which ones timed out
                    elapsed = time.time() - wait_start
                    logger.warning(
                        "Entry id=%s: source queries timed out after %.1fs",
                        result["id"],
                        elapsed,
                    )

                    for fut, src in future_to_source.items():
                        if not fut.done():
                            timed_out_sources.add(src)
                            fut.cancel()
                            result["attempts"][src]["error"] = (
                                f"timeout after {self.entry_timeout:.1f}s"
                            )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            # Choose corrected fields with priority: crossref -> dblp -> semantic
            if result["matches"] and not result["corrected_fields"]:
                for preferred in DEFAULT_ORDER:
                    if preferred in result["matches"]:
                        result["corrected_fields"] = result["matches"][preferred][
                            "corrected_fields"
                        ]
                        result["search_method"] = result["matches"][preferred][
                            "search_method"
                        ]
                        result["match_accepted"] = True

                        # Compute match confidence and kind (for parallel mode)
                        if result["search_method"].endswith(":DOI"):
                            result["match_kind"] = "doi"
                            result["match_confidence"] = 1.0
                        else:
                            result["match_kind"] = "title"
                            orig_title = (entry.get("title") or "").strip()
                            corr_title = (
                                result["corrected_fields"].get("title") or ""
                            ).strip()
                            if orig_title and corr_title:
                                title_sim = (
                                    token_set_ratio(
                                        orig_title.lower(), corr_title.lower()
                                    )
                                    / 100.0
                                )
                                result["match_confidence"] = title_sim
                            else:
                                result["match_confidence"] = 0.0
                        break

        # PHASE: No acceptable remote match
        # This can mean:
        #  - truly not present in sources, OR
        #  - sources returned candidates but they were rejected by the safety gate.
        if not result["matches"]:
            # Build list of sources that were actually attempted
            attempted_sources = [
                name
                for name, info in result["attempts"].items()
                if info.get("attempted")
            ]

            # Add remote "not found" issue with checked sources list
            if attempted_sources:
                result["issues"].append(
                    f"REMOTE: not found in checked sources: {', '.join(attempted_sources)}"
                )
            else:
                result["issues"].append(
                    "REMOTE: not checked in any source (all skipped)"
                )

            if result.get("rejected_candidates"):
                result["issues"].append(
                    f"REMOTE: rejected {len(result['rejected_candidates'])} low-confidence candidate match(es)"
                )
                if not result["needs_review"]:
                    result["needs_review"] = True
                    result["review_reasons"].append(
                        "REMOTE: candidate matches were rejected by safety gate"
                    )

            if result.get("candidate_matches"):
                result["issues"].append(
                    f"REMOTE: {len(result['candidate_matches'])} plausible candidate(s) found but ambiguous"
                )

            # Determine status: only blocking lint makes it a mismatch if not found
            blocking_lint = [i for i in lint_issues if i.startswith("BLOCKING:")]
            result["status"] = "mismatch" if blocking_lint else "not_found"

            # Add timeout issues for sources that timed out
            for src in timed_out_sources:
                result["issues"].append(
                    f"{src.upper()}: TIMEOUT after {self.entry_timeout:.1f}s"
                )

            # Set needs_review for not_found
            if not result["needs_review"]:
                result["needs_review"] = True
            result["review_reasons"].append("REMOTE: not found in any checked source")
            result["review_reasons"] = list(dict.fromkeys(result["review_reasons"]))

            return result

        # PHASE: Compare local vs. accepted remote match(es)
        # Issues here represent *disagreements* (title/authors/year/venue) between local entry and
        # what the remote source believes. These drive "mismatch" status.
        validation_issues = []
        for source_name, m in result["matches"].items():
            source_issues = self.compare_with_corrected(entry, m["corrected_fields"])
            validation_issues.extend(
                [f"{source_name.upper()}: {i}" for i in source_issues]
            )

        # Add timeout issues for sources that timed out
        for src in timed_out_sources:
            validation_issues.append(
                f"{src.upper()}: TIMEOUT after {self.entry_timeout:.1f}s"
            )

        # De-duplicate issues while preserving order
        validation_issues = list(dict.fromkeys(validation_issues))

        # Remove lint issues that are resolved by corrected_fields (before combining)
        resolved_missing = self._compute_resolved_lint_issues(
            entry, result["corrected_fields"], lint_issues
        )
        result["issues"] = [
            i
            for i in result["issues"]
            if not (i.startswith("LINT:") and i[5:] in resolved_missing)
        ]

        # Combine lint and validation issues
        result["issues"].extend(validation_issues)

        # Determine final status: remote mismatches are the blocker
        # Lint is advisory; if remote match is clean, entry is validated
        result["status"] = "validated" if not validation_issues else "mismatch"

        # Set needs_review flags
        if result["status"] == "mismatch":
            result["needs_review"] = True
            result["review_reasons"].append("REMOTE: mismatches with matched source")

        if result.get("match_kind") == "composite":
            conf = result.get("match_confidence") or 0.0
            if conf < TITLE_SIMILARITY_THRESHOLD:
                result["needs_review"] = True
                result["review_reasons"].append(
                    f"LOW_CONFIDENCE_MATCH: {conf:.2f} < {TITLE_SIMILARITY_THRESHOLD:.2f}"
                )

        result["review_reasons"] = list(dict.fromkeys(result["review_reasons"]))

        return result

    def _prompt_user_to_select_candidate(
        self, entry: Dict, candidates: List[Dict]
    ) -> Optional[Dict]:
        """
        Prompt user to select among ambiguous candidate matches using the interactive UI.
        
        Args:
            entry: Original BibTeX entry
            candidates: List of candidate dicts with keys: source, search_method, corrected_fields, score, components
            
        Returns:
            The selected candidate dict, or None if user chooses to skip
        """
        if not candidates:
            return None

        entry_id = entry.get("ID", "unknown")
        idx = choose_candidate_interactive(entry, candidates, entry_id=entry_id)
        if idx is None:
            return None
        return candidates[idx]

    def _maybe_check_url(self, entry: Dict, result: Dict) -> None:
        """
        Check URL reachability if entry has a URL field.

        Skips DOI resolver URLs (handled via doi field).
        Caches results to avoid redundant checks.
        Sets needs_review if URL is unreachable.

        Args:
            entry: BibTeX entry dict
            result: validation result dict to add issues/review reasons to
        """
        url = (entry.get("url") or "").strip()

        # No URL to check
        if not url:
            return

        # Skip DOI resolver URLs (they're handled via the doi field)
        if is_doi_url(url):
            logger.debug(
                "URL check: skipping DOI resolver URL for entry id=%s", entry.get("ID")
            )
            return

        # Check cache
        if url in self.url_check_cache:
            ok, detail = self.url_check_cache[url]
        else:
            # Perform check
            ok, detail = check_url(url, self.url_check_session, timeout=6.0)
            self.url_check_cache[url] = (ok, detail)
            logger.debug("URL check for %s: %s", url, detail)

        # If not ok, add issue and flag for review
        if not ok:
            result["issues"].append(f"URL_CHECK: {detail}")
            result["needs_review"] = True
            result["review_reasons"].append(
                f"URL_CHECK: unreachable or error ({detail})"
            )

    def _compute_resolved_lint_issues(
        self, entry: Dict, corrected_fields: Dict, lint_issues: List[str]
    ) -> set:
        """
        Compute which lint issues are resolved by corrected_fields.
        Returns set of lint issue strings (without LINT: prefix) that can be removed.
        """
        resolved = set()
        entry_type = (entry.get("ENTRYTYPE") or "").lower()

        if corrected_fields.get("title"):
            resolved.add("BLOCKING:MISSING_FIELD:title")

        if corrected_fields.get("author"):
            resolved.add("BLOCKING:MISSING_FIELD:author")

        if corrected_fields.get("year"):
            resolved.add("BLOCKING:MISSING_FIELD:year")

        # Venue can come as venue OR explicit journal/booktitle
        if corrected_fields.get("journal") or corrected_fields.get("venue"):
            resolved.add("BLOCKING:MISSING_FIELD:journal")

        if corrected_fields.get("booktitle") or corrected_fields.get("venue"):
            resolved.add("BLOCKING:MISSING_FIELD:booktitle")

        return resolved

    def get_ranked_candidates(self, entry: Dict, limit: int = 15) -> List[Dict]:
        """
        Public API for UIs: return ranked candidates across all enabled sources.
        Does not mutate self.results.
        """
        tasks = []
        for source_name in DEFAULT_ORDER:
            if source_name not in self.sources:
                continue
            source = self.sources[source_name]
            can_try, _why = source.should_attempt(entry)
            if can_try:
                tasks.append((source_name, source))

        candidates: List[Dict] = []
        for source_name, source in tasks:
            cand_tuples = self._query_source(source_name, source, entry) or []
            for found, corrected_fields, search_method in cand_tuples:
                accepted, reject_reasons, _title_sim = self._accept_match(
                    entry, corrected_fields, search_method
                )
                scored = self._score_candidate(entry, corrected_fields)
                hard = scored.get("hard_reject_reasons") or []
                candidates.append(
                    {
                        "source": source_name,
                        "search_method": search_method,
                        "source_data": found,
                        "corrected_fields": corrected_fields,
                        "score": float(scored["score"]),
                        "components": scored["components"],
                        "accepted_by_gate": bool(accepted and not hard),
                        "gate_reasons": list(reject_reasons) + list(hard),
                    }
                )

        def _key(c: Dict):
            is_doi = bool((c.get("search_method") or "").endswith(":DOI"))
            return (1 if is_doi else 0, float(c.get("score") or 0.0))

        candidates.sort(key=_key, reverse=True)
        return candidates[:limit]

    def _query_source(
        self, source_name: str, source: ValidationSource, entry: Dict
    ) -> List[tuple]:
        """
        Query a single source for an entry (with caching).
        Returns list of (found_result, corrected_fields, search_method) tuples.
        Now returns candidates instead of single result.
        """
        out: List[tuple] = []
        found = None
        search_method = None

        cache = self.source_caches[source_name]

        logger.debug("Query source=%s entry_id=%s", source_name, entry.get("ID"))

        # Try DOI first if available
        raw_doi = entry.get("doi")
        if raw_doi:
            from .normalize import normalize_doi
            doi = normalize_doi(raw_doi)
            if doi:
                if doi in cache["doi"]:
                    found = cache["doi"][doi]
                    logger.debug(
                        "Cache hit: source=%s doi=%s found=%s",
                        source_name,
                        doi,
                        bool(found),
                    )
                    search_method = f"{source_name}:DOI"
                else:
                    found = source.search_by_doi(doi)
                    logger.debug(
                        "DOI query: source=%s doi=%s found=%s",
                        source_name,
                        doi,
                        bool(found),
                    )
                    cache["doi"][doi] = found
                    if found:
                        search_method = f"{source_name}:DOI"
                if self.source_delay > 0:
                    time.sleep(self.source_delay)
            else:
                logger.debug(
                    "DOI normalization failed: source=%s raw_doi=%r",
                    source_name,
                    raw_doi,
                )

        # If DOI match exists, return it as the only candidate
        if found:
            corrected_fields = source.extract_bibtex_fields(found)
            out.append((found, corrected_fields, search_method))
            return out

        # Try title if DOI search didn't work (return a shortlist of candidates)
        raw_title = entry.get("title")
        title = normalize_title_for_query(raw_title or "")
        if title:
            if title in cache["title"]:
                candidates = cache["title"][title] or []
                logger.debug(
                    "Cache hit: source=%s title=%r found=%d candidates",
                    source_name,
                    title,
                    len(candidates),
                )
            else:
                candidates = source.search_by_title_candidates(
                    title, max_results=MAX_CANDIDATES_PER_SOURCE
                )
                logger.debug(
                    "Title query: source=%s title=%r found=%d candidates",
                    source_name,
                    title,
                    len(candidates),
                )
                cache["title"][title] = candidates
            if self.source_delay > 0:
                time.sleep(self.source_delay)

            for cand in candidates:
                corrected_fields = source.extract_bibtex_fields(cand)
                out.append((cand, corrected_fields, f"{source_name}:Title"))

        return out

    def _author_overlap_ratio(
        self, orig_authors: List[str], corr_authors: List[str]
    ) -> Optional[float]:
        """
        Return last-name overlap ratio in [0,1], or None if cannot be computed
        (e.g., missing authors).
        """
        if not orig_authors or not corr_authors:
            return None
        orig_last = {extract_last_name(a) for a in orig_authors if a}
        corr_last = {extract_last_name(a) for a in corr_authors if a}
        if not orig_last or not corr_last:
            return None
        overlap = len(orig_last & corr_last)
        return overlap / max(len(orig_last), len(corr_last))

    def _score_candidate(self, entry: Dict, corrected_fields: Dict) -> Dict:
        """
        Compute a composite score for ranking multiple candidates.
        Returns dict with: score (0..1), components, and hard_reject_reasons (if any).
        """
        hard_reject: List[str] = []

        # Title similarity (rapidfuzz, consistent with reporting)
        orig_title = (entry.get("title") or "").strip()
        corr_title = (corrected_fields.get("title") or "").strip()
        if orig_title and corr_title:
            title_sim = token_set_ratio(orig_title.lower(), corr_title.lower()) / 100.0
        else:
            title_sim = 0.0
            hard_reject.append("Missing title for scoring")

        # Author overlap
        orig_authors = authors_to_list(entry.get("author"))
        corr_authors = authors_to_list(corrected_fields.get("author"))
        overlap = self._author_overlap_ratio(orig_authors, corr_authors)
        author_sim = overlap if overlap is not None else 0.0

        # Year agreement
        orig_year = (entry.get("year") or "").strip()
        corr_year = (corrected_fields.get("year") or "").strip()
        year_sim = 1.0 if (orig_year and corr_year and orig_year == corr_year) else (0.0 if (orig_year and corr_year) else 0.5)

        # Venue similarity (weak)
        orig_venue = (entry.get("booktitle") or entry.get("journal") or "").strip()
        corr_venue = (corrected_fields.get("booktitle") or corrected_fields.get("journal") or corrected_fields.get("venue") or "").strip()
        if orig_venue and corr_venue:
            venue_sim = token_set_ratio(orig_venue.lower(), corr_venue.lower()) / 100.0
        else:
            venue_sim = 0.5

        # Hard rejects for very poor fits when authors exist
        if title_sim < 0.70:
            hard_reject.append(f"Title similarity too low for candidate ({title_sim:.2f} < 0.70)")
        if overlap is not None and overlap < 0.20:
            hard_reject.append(f"Author overlap too low for candidate ({overlap:.2f} < 0.20)")

        # Weighted score (tunable)
        score = (0.45 * title_sim) + (0.35 * author_sim) + (0.15 * year_sim) + (0.05 * venue_sim)

        return {
            "score": float(score),
            "components": {
                "title": float(title_sim),
                "author": float(author_sim),
                "year": float(year_sim),
                "venue": float(venue_sim),
            },
            "hard_reject_reasons": hard_reject,
        }

    def _accept_match(
        self,
        entry: Dict,
        corrected_fields: Dict,
        search_method: Optional[str],
    ) -> tuple:
        """
        Decide if a candidate remote result is safe to treat as a match.
        
        This is the core safety gate that prevents wrong-paper substitutions.
        Uses conservative thresholds and exhaustive checks (difflib for gate, rapidfuzz for reporting).
        
        Returns: (accepted, rejection_reasons, title_similarity)
        """
        reasons: List[str] = []

        # DOI-based matches are treated as authoritative by this tool.
        if search_method and search_method.endswith(":DOI"):
            # Safety: if both sides have DOI and they disagree, reject.
            orig_doi = normalize_doi(entry.get("doi") or "")
            corr_doi = normalize_doi(corrected_fields.get("doi") or "")
            if orig_doi and corr_doi and orig_doi != corr_doi:
                reasons.append(f"DOI mismatch (orig={orig_doi}, corr={corr_doi})")
                return False, reasons, 0.0
            return True, [], 1.0

        # Title must exist on both sides for non-DOI acceptance.
        orig_title = (entry.get("title") or "").strip()
        corr_title = (corrected_fields.get("title") or "").strip()
        if not orig_title or not corr_title:
            reasons.append("Missing title for non-DOI match")
            return False, reasons, 0.0

        # Similarity (use the same "query-normalized" titles to avoid punctuation noise)
        a = normalize_title_for_query(orig_title).lower()
        b = normalize_title_for_query(corr_title).lower()
        title_sim = difflib.SequenceMatcher(None, a, b).ratio()

        if title_sim < MIN_MATCH_TITLE_SIMILARITY:
            reasons.append(
                f"Title similarity too low ({title_sim:.2f} < {MIN_MATCH_TITLE_SIMILARITY:.2f})"
            )

        # Authors: reject if both present but overlap is too low
        orig_authors = authors_to_list(entry.get("author"))
        corr_authors = authors_to_list(corrected_fields.get("author"))
        overlap = self._author_overlap_ratio(orig_authors, corr_authors)
        if overlap is not None and overlap < MIN_MATCH_AUTHOR_OVERLAP:
            reasons.append(
                f"Author last-name overlap too low ({overlap:.2f} < {MIN_MATCH_AUTHOR_OVERLAP:.2f})"
            )

        accepted = len(reasons) == 0
        return accepted, reasons, title_sim

    def compare_with_corrected(self, original: Dict, corrected: Dict) -> List[str]:
        """Compare original entry with corrected fields from validation source"""
        issues = []

        # If DOI matches, treat metadata differences as mostly formatting, not mismatches.
        orig_doi = normalize_doi(original.get("doi") or "")
        corr_doi = normalize_doi(corrected.get("doi") or "")
        doi_matched = bool(orig_doi and corr_doi and orig_doi == corr_doi)

        # Compare title (using rapidfuzz for similarity check)
        orig_title = original.get("title", "").strip()
        corr_title = corrected.get("title", "").strip()
        if orig_title and corr_title:
            title_similarity = (
                token_set_ratio(orig_title.lower(), corr_title.lower()) / 100.0
            )
            if title_similarity < TITLE_SIMILARITY_THRESHOLD:
                issues.append(
                    f"TITLE: Low similarity ({title_similarity:.2f}). Original: '{orig_title[:50]}' vs Corrected: '{corr_title[:50]}'"
                )

        # Compare authors (by last name set overlap)
        # Normalize first to reduce false mismatches due to BibTeX formatting,
        # LaTeX escapes, hyphenation differences, etc.
        if "author" in corrected and "author" in original:
            orig_author_raw = normalize_author_string(original.get("author") or "")
            corr_author_raw = normalize_author_string(corrected.get("author") or "")
            orig_authors = authors_to_list(orig_author_raw)
            corr_authors = authors_to_list(corr_author_raw)

            author_issue = compare_author_sets(orig_authors, corr_authors)
            if author_issue and not doi_matched:
                issues.append(author_issue)

        # Compare year (strict)
        orig_year = original.get("year", "")
        corr_year = corrected.get("year", "")
        if orig_year and corr_year and orig_year != corr_year:
            issues.append(f"YEAR: {orig_year} vs {corr_year}")

        # Compare venue (fuzzy, using rapidfuzz)
        # Check both original and corrected for venue-like fields
        orig_venue = original.get("booktitle") or original.get("journal") or ""
        corr_venue = (
            corrected.get("booktitle")
            or corrected.get("journal")
            or corrected.get("venue")
            or ""
        )
        if orig_venue and corr_venue:
            similarity = token_set_ratio(orig_venue.lower(), corr_venue.lower()) / 100.0
            if similarity < 0.60:
                issues.append(
                    f"VENUE: '{orig_venue}' vs '{corr_venue}' (similarity: {similarity:.2f})"
                )

        return issues

    def apply_corrections_to_entries(self, mode: str = "preferred") -> List[Dict]:
        """
        Apply corrected fields to entries and return updated copy.

        Strategy:
        - For DOI-based matches (match_kind == "doi"):
          - Full authoritative replacement: overwrite entire entry except ID
        - For title-based matches (match_kind == "composite"):
          - If match_confidence >= TITLE_SIMILARITY_THRESHOLD: full replacement (trusted)
          - Otherwise: only fill missing fields (non-destructive, conservative)
        - Never overwrite: ID (BibTeX citation key)
        - Always preserve or set ENTRYTYPE (required by bibtexparser)

        Args:
            mode: "preferred" (apply all validated/mismatch corrections, with trust guards)
                  "validated-only" (only apply to fully validated entries)
                  "never" (skip corrections entirely, return unchanged copy)
        """
        updated_entries = []

        for entry in self.entries:
            entry_copy = deepcopy(entry)
            entry_id = entry.get("ID")

            # Mode: never apply corrections
            if mode == "never":
                updated_entries.append(entry_copy)
                continue

            # Choose which results to consider based on mode
            if mode == "validated-only":
                candidates = self.results["validated"]
            else:  # mode == "preferred"
                candidates = self.results["validated"] + self.results["mismatches"]

            # Find validation result for this entry
            validation_result = None
            for result in candidates:
                if result["id"] == entry_id:
                    validation_result = result
                    break

            # Apply corrections if found
            if (
                validation_result
                and validation_result.get("match_accepted")
                and "corrected_fields" in validation_result
            ):
                # Guardrail: corrections can ONLY be applied if the chosen remote candidate
                # was accepted by the safety gate during validation.
                corrected = validation_result["corrected_fields"]

                # Determine if match is trusted (for full replacement)
                match_kind = validation_result.get("match_kind")
                match_confidence = validation_result.get("match_confidence")
                # Trust model:
                #  - DOI match: authoritative -> full replacement
                #  - Composite match: only trusted for full replacement if confidence clears threshold;
                #    otherwise we only fill missing fields to avoid overwriting correct human-curated data.
                trusted_match = (match_kind == "doi") or (
                    match_confidence is not None
                    and match_confidence >= TITLE_SIMILARITY_THRESHOLD
                )

                autofilled = []

                if trusted_match:
                    # Full authoritative replacement: keep only local citation key
                    old_id = entry_copy.get("ID")
                    old_type = entry_copy.get(
                        "ENTRYTYPE"
                    )  # Fallback if remote doesn't provide it
                    entry_copy = {"ID": old_id}

                    # Copy all remote fields verbatim
                    for field, value in corrected.items():
                        if not value:
                            continue
                        if field == "ID":
                            continue
                        entry_copy[field] = value
                        autofilled.append(f"{field} (trusted replace)")

                    # Guarantee ENTRYTYPE exists for bibtexparser
                    if "ENTRYTYPE" not in entry_copy or not entry_copy["ENTRYTYPE"]:
                        if old_type:
                            entry_copy["ENTRYTYPE"] = old_type
                            autofilled.append("ENTRYTYPE (fallback from local)")
                        else:
                            entry_copy["ENTRYTYPE"] = "misc"
                            autofilled.append("ENTRYTYPE (default misc)")

                else:
                    # Non-trusted match: keep current conservative behaviour (fill missing only)
                    for field, value in corrected.items():
                        if not value:
                            continue
                        if field == "ID":
                            continue
                        if not entry_copy.get(field):
                            entry_copy[field] = value
                            autofilled.append(f"{field} (was missing)")

                # Add x-found-in field with provenance
                if validation_result.get("search_method"):
                    entry_copy["x-found-in"] = validation_result["search_method"]

                # Record autofilled fields for reporting
                validation_result["autofilled_fields"] = autofilled

            updated_entries.append(entry_copy)

        return updated_entries
