"""Core validation logic"""

import logging
import time
import requests
from typing import Dict, List, Optional, Any
from copy import deepcopy
from rapidfuzz.fuzz import token_set_ratio
from .sources import ValidationSource, build_sources, DEFAULT_ORDER
from .lint import lint_entries, is_web_resource, has_blocking_lint_issues
from .normalize import normalize_doi, normalize_title_for_query, normalize_author_string
from .url_check import check_url, is_doi_url


logger = logging.getLogger(__name__)

# Title similarity threshold for matching (0.0 to 1.0)
TITLE_SIMILARITY_THRESHOLD = 0.75


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
    """
    if not author_str:
        return ""
    
    author_str = author_str.strip()
    
    # If contains comma, last name is before comma (e.g., "Smith, J.")
    if "," in author_str:
        last_name = author_str.split(",")[0].strip()
    else:
        # Otherwise, last name is last word (e.g., "John Smith" or "J. Smith")
        parts = author_str.split()
        last_name = parts[-1] if parts else ""
    
    # Remove trailing periods and normalize
    last_name = last_name.rstrip(".")
    
    return last_name.lower()


def compare_author_sets(orig_authors: List[str], corr_authors: List[str]) -> Optional[str]:
    """
    Compare two author lists by last name set overlap.
    Returns issue string if mismatch detected, else None.
    """
    if not orig_authors or not corr_authors:
        return None
    
    # Count mismatch is always an issue
    if len(orig_authors) != len(corr_authors):
        return f"AUTHORS: Different count ({len(orig_authors)} vs {len(corr_authors)})"
    
    # Extract last names (normalized to lowercase)
    orig_last_names = {extract_last_name(a) for a in orig_authors if a}
    corr_last_names = {extract_last_name(a) for a in corr_authors if a}
    
    # If either set is empty after normalization, skip detailed check
    if not orig_last_names or not corr_last_names:
        return None
    
    # Compute overlap
    overlap = orig_last_names & corr_last_names
    overlap_ratio = len(overlap) / max(len(orig_last_names), len(corr_last_names))
    
    # If overlap is low (< 60%), flag as mismatch
    if overlap_ratio < 0.6:
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
        """
        self.entries = entries
        self.sources = sources or build_sources(DEFAULT_ORDER)
        self.source_delay = source_delay
        self.entry_delay = entry_delay
        self.max_workers = max_workers
        self.entry_timeout = entry_timeout
        self.stop_on_first_match = stop_on_first_match
        self.results = {
            "validated": [],
            "mismatches": [],
            "not_found": [],
        }
        self.lint_results = {}
        self.lint_issue_count = 0
        self.lint_entry_count = 0
        # Per-source caches: {source_name: {"doi": {...}, "title": {...}}}
        self.source_caches = {name: {"doi": {}, "title": {}} for name in self.sources}
        # URL reachability check cache: {url: (ok: bool, detail: str)}
        self.url_check_cache: Dict[str, tuple] = {}
        # Session for URL checking (reuses connection)
        self.url_check_session = requests.Session()

        logger.debug(
            "Validator initialized: entries=%d sources=%s source_delay=%.3f entry_delay=%.3f max_workers=%d entry_timeout=%.3f stop_on_first_match=%s",
            len(self.entries),
            list(self.sources.keys()),
            self.source_delay,
            self.entry_delay,
            self.max_workers,
            self.entry_timeout,
            self.stop_on_first_match,
        )

    def validate_all(self):
        """Validate all entries against sources"""
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
            print("📊 Using preferred-order matching (stop on first match)")
        else:
            print(f"📊 Using {self.max_workers} parallel source queries per entry")
        
        total = len(self.entries)
        for idx, entry in enumerate(self.entries, 1):
            entry_id = entry.get("ID", "unknown")
            title = entry.get("title", "No title")[:50]

            t0 = time.time()
            logger.debug("Validating entry id=%s title=%r", entry_id, entry.get("title", ""))

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

        # For web resources: skip remote validation, validate based on lint only
        if is_web:
            logger.debug("Entry id=%s is web resource; skipping remote validation", result["id"])
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

        # For academic entries: proceed with remote validation
        # Collect tasks: (source_name, source, can_try, why)
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
                logger.debug("Source %s skipping entry id=%s (%s)", source_name, result["id"], why)
            else:
                tasks.append((source_name, source))

        # Query sources based on mode
        timed_out_sources = set()
        
        if self.stop_on_first_match:
            # Sequential: query in preferred order, stop at first match
            for source_name, source in tasks:
                try:
                    found, corrected_fields, search_method = self._query_source(source_name, source, entry)
                    if found:
                        result["matches"][source_name] = {
                            "search_method": search_method,
                            "source_data": found,
                            "corrected_fields": corrected_fields,
                        }
                        result["corrected_fields"] = corrected_fields
                        result["search_method"] = search_method
                        
                        # Compute match confidence and kind
                        if search_method.endswith(":DOI"):
                            result["match_kind"] = "doi"
                            result["match_confidence"] = 1.0
                        else:
                            # Title-based match: compute similarity
                            result["match_kind"] = "title"
                            orig_title = (entry.get("title") or "").strip()
                            corr_title = (corrected_fields.get("title") or "").strip()
                            if orig_title and corr_title:
                                title_sim = token_set_ratio(orig_title.lower(), corr_title.lower()) / 100.0
                                result["match_confidence"] = title_sim
                            else:
                                result["match_confidence"] = 0.0
                        
                        logger.debug(
                            "Found match in source %s for entry id=%s; match_kind=%s confidence=%.2f; stopping search",
                            source_name,
                            result["id"],
                            result["match_kind"],
                            result["match_confidence"],
                        )
                        break
                    else:
                        logger.debug("No match in source %s for entry id=%s; trying next source", source_name, result["id"])
                except Exception as e:
                    logger.debug("Source %s failed for entry id=%s: %s", source_name, result["id"], e, exc_info=True)
                    result["attempts"][source_name]["error"] = str(e)
        else:
            # Legacy parallel mode (kept for backwards compatibility)
            from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as ConcurrentTimeoutError
            
            executor = ThreadPoolExecutor(max_workers=self.max_workers)
            try:
                # Submit all tasks
                future_to_source = {
                    executor.submit(self._query_source, source_name, source, entry): source_name
                    for source_name, source in tasks
                }
                
                # Collect results as they complete with timeout
                wait_start = time.time()
                try:
                    for future in as_completed(future_to_source, timeout=self.entry_timeout):
                        source_name = future_to_source[future]
                        try:
                            found, corrected_fields, search_method = future.result()
                            if found:
                                result["matches"][source_name] = {
                                    "search_method": search_method,
                                    "source_data": found,
                                    "corrected_fields": corrected_fields,
                                }
                        except Exception as e:
                            # Log but don't crash on individual source failures
                            logger.debug("Source %s failed for entry id=%s: %s", source_name, result["id"], e, exc_info=True)
                            result["attempts"][source_name]["error"] = str(e)
                except ConcurrentTimeoutError:
                    # Some sources didn't finish in time; record which ones timed out
                    elapsed = time.time() - wait_start
                    logger.warning("Entry id=%s: source queries timed out after %.1fs", result["id"], elapsed)

                    for fut, src in future_to_source.items():
                        if not fut.done():
                            timed_out_sources.add(src)
                            fut.cancel()
                            result["attempts"][src]["error"] = f"timeout after {self.entry_timeout:.1f}s"
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            # Choose corrected fields with priority: crossref -> dblp -> semantic
            if result["matches"] and not result["corrected_fields"]:
                for preferred in DEFAULT_ORDER:
                    if preferred in result["matches"]:
                        result["corrected_fields"] = result["matches"][preferred][
                            "corrected_fields"
                        ]
                        result["search_method"] = result["matches"][preferred]["search_method"]
                        
                        # Compute match confidence and kind (for parallel mode)
                        if result["search_method"].endswith(":DOI"):
                            result["match_kind"] = "doi"
                            result["match_confidence"] = 1.0
                        else:
                            result["match_kind"] = "title"
                            orig_title = (entry.get("title") or "").strip()
                            corr_title = (result["corrected_fields"].get("title") or "").strip()
                            if orig_title and corr_title:
                                title_sim = token_set_ratio(orig_title.lower(), corr_title.lower()) / 100.0
                                result["match_confidence"] = title_sim
                            else:
                                result["match_confidence"] = 0.0
                        break

        # Determine status and collect issues
        if not result["matches"]:
            # Build list of sources that were actually attempted
            attempted_sources = [
                name for name, info in result["attempts"].items()
                if info.get("attempted")
            ]
            
            # Add remote "not found" issue with checked sources list
            if attempted_sources:
                result["issues"].append(
                    f"REMOTE: not found in checked sources: {', '.join(attempted_sources)}"
                )
            else:
                result["issues"].append("REMOTE: not checked in any source (all skipped)")
            
            # Determine status: only blocking lint makes it a mismatch if not found
            blocking_lint = [i for i in lint_issues if i.startswith("BLOCKING:")]
            result["status"] = "mismatch" if blocking_lint else "not_found"
            
            # Add timeout issues for sources that timed out
            for src in timed_out_sources:
                result["issues"].append(f"{src.upper()}: TIMEOUT after {self.entry_timeout:.1f}s")
            
            # Set needs_review for not_found
            result["needs_review"] = True
            result["review_reasons"].append("REMOTE: not found in any checked source")
            result["review_reasons"] = list(dict.fromkeys(result["review_reasons"]))
            
            return result

        # Compare against each source match; prefix issues with source name
        validation_issues = []
        for source_name, m in result["matches"].items():
            source_issues = self.compare_with_corrected(entry, m["corrected_fields"])
            validation_issues.extend([f"{source_name.upper()}: {i}" for i in source_issues])

        # Add timeout issues for sources that timed out
        for src in timed_out_sources:
            validation_issues.append(f"{src.upper()}: TIMEOUT after {self.entry_timeout:.1f}s")

        # De-duplicate issues while preserving order
        validation_issues = list(dict.fromkeys(validation_issues))

        # Remove lint issues that are resolved by corrected_fields (before combining)
        resolved_missing = self._compute_resolved_lint_issues(entry, result["corrected_fields"], lint_issues)
        result["issues"] = [
            i for i in result["issues"] 
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
        
        if result.get("match_kind") == "title":
            conf = result.get("match_confidence") or 0.0
            if conf < TITLE_SIMILARITY_THRESHOLD:
                result["needs_review"] = True
                result["review_reasons"].append(
                    f"LOW_CONFIDENCE_TITLE_MATCH: {conf:.2f} < {TITLE_SIMILARITY_THRESHOLD:.2f}"
                )
        
        result["review_reasons"] = list(dict.fromkeys(result["review_reasons"]))
        
        return result

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
            logger.debug("URL check: skipping DOI resolver URL for entry id=%s", entry.get("ID"))
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
            result["review_reasons"].append(f"URL_CHECK: unreachable or error ({detail})")

    def _compute_resolved_lint_issues(self, entry: Dict, corrected_fields: Dict, lint_issues: List[str]) -> set:
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

    def _query_source(
        self, source_name: str, source: ValidationSource, entry: Dict
    ) -> tuple:
        """
        Query a single source for an entry (with caching).
        Normalizes DOI and title before querying.
        Returns: (found_result, corrected_fields, search_method) or (None, {}, None)
        """
        found = None
        search_method = None
        corrected_fields = {}

        cache = self.source_caches[source_name]

        logger.debug("Query source=%s entry_id=%s", source_name, entry.get("ID"))

        # Try DOI first if available
        raw_doi = entry.get("doi")
        if raw_doi:
            doi = normalize_doi(raw_doi)
            if doi:
                if doi in cache["doi"]:
                    found = cache["doi"][doi]
                    logger.debug("Cache hit: source=%s doi=%s found=%s", source_name, doi, bool(found))
                    search_method = f"{source_name}:DOI"
                else:
                    found = source.search_by_doi(doi)
                    logger.debug("DOI query: source=%s doi=%s found=%s", source_name, doi, bool(found))
                    cache["doi"][doi] = found
                    if found:
                        search_method = f"{source_name}:DOI"
                if self.source_delay > 0:
                    time.sleep(self.source_delay)
            else:
                logger.debug("DOI normalization failed: source=%s raw_doi=%r", source_name, raw_doi)

        # Try title if DOI search didn't work
        if not found:
            raw_title = entry.get("title")
            title = normalize_title_for_query(raw_title or "")
            if title:
                if title in cache["title"]:
                    found = cache["title"][title]
                    logger.debug("Cache hit: source=%s title=%r found=%s", source_name, title, bool(found))
                    search_method = f"{source_name}:Title"
                else:
                    found = source.search_by_title(title)
                    logger.debug("Title query: source=%s title=%r found=%s", source_name, title, bool(found))
                    cache["title"][title] = found
                    if found:
                        search_method = f"{source_name}:Title"
                if self.source_delay > 0:
                    time.sleep(self.source_delay)

        if found:
            corrected_fields = source.extract_bibtex_fields(found)
            logger.debug(
                "Extracted fields: source=%s entry_id=%s keys=%s",
                source_name,
                entry.get("ID"),
                sorted(corrected_fields.keys()),
            )

        return found, corrected_fields, search_method

    def compare_with_corrected(self, original: Dict, corrected: Dict) -> List[str]:
        """Compare original entry with corrected fields from validation source"""
        issues = []

        # Compare title (using rapidfuzz for similarity check)
        orig_title = original.get("title", "").strip()
        corr_title = corrected.get("title", "").strip()
        if orig_title and corr_title:
            title_similarity = token_set_ratio(orig_title.lower(), corr_title.lower()) / 100.0
            if title_similarity < TITLE_SIMILARITY_THRESHOLD:
                issues.append(
                    f"TITLE: Low similarity ({title_similarity:.2f}). Original: '{orig_title[:50]}' vs Corrected: '{corr_title[:50]}'"
                )

        # Compare authors (by last name set overlap)
        if "author" in corrected and "author" in original:
            orig_authors = authors_to_list(original.get("author"))
            corr_authors = authors_to_list(corrected.get("author"))
            
            author_issue = compare_author_sets(orig_authors, corr_authors)
            if author_issue:
                issues.append(author_issue)

        # Compare year (strict)
        orig_year = original.get("year", "")
        corr_year = corrected.get("year", "")
        if orig_year and corr_year and orig_year != corr_year:
            issues.append(f"YEAR: {orig_year} vs {corr_year}")

        # Compare venue (fuzzy, using rapidfuzz)
        # Check both original and corrected for venue-like fields
        orig_venue = original.get("booktitle") or original.get("journal") or ""
        corr_venue = corrected.get("booktitle") or corrected.get("journal") or corrected.get("venue") or ""
        if orig_venue and corr_venue:
            similarity = token_set_ratio(orig_venue.lower(), corr_venue.lower()) / 100.0
            if similarity < 0.60:
                issues.append(f"VENUE: '{orig_venue}' vs '{corr_venue}' (similarity: {similarity:.2f})")

        return issues

    def apply_corrections_to_entries(self, mode: str = "preferred") -> List[Dict]:
        """
        Apply corrected fields to entries and return updated copy.
        
        Strategy:
        - For DOI-based matches (match_kind == "doi"): 
          - Full authoritative replacement: overwrite entire entry except ID
        - For title-based matches (match_kind == "title"):
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
            if validation_result and "corrected_fields" in validation_result:
                corrected = validation_result["corrected_fields"]
                
                # Determine if match is trusted (for full replacement)
                match_kind = validation_result.get("match_kind")
                match_confidence = validation_result.get("match_confidence")
                trusted_match = (match_kind == "doi") or (
                    match_confidence is not None and match_confidence >= TITLE_SIMILARITY_THRESHOLD
                )
                
                autofilled = []

                if trusted_match:
                    # Full authoritative replacement: keep only local citation key
                    old_id = entry_copy.get("ID")
                    old_type = entry_copy.get("ENTRYTYPE")  # Fallback if remote doesn't provide it
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
