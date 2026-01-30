"""Core validation logic"""

import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Any
from copy import deepcopy
from .sources import ValidationSource, build_sources, DEFAULT_ORDER


def authors_to_list(authors_val: Any) -> List[str]:
    """Convert author value (string, list, or object) to list of strings"""
    if not authors_val:
        return []
    if isinstance(authors_val, list):
        return [str(a).strip() for a in authors_val if str(a).strip()]
    return [a.strip() for a in str(authors_val).split(" and ") if a.strip()]


class SmartBibtexValidator:
    """Validates BibTeX entries against multiple academic sources"""

    def __init__(
        self, entries: List[Dict], sources: Optional[Dict[str, ValidationSource]] = None
    ):
        """
        Initialize validator

        Args:
            entries: List of BibTeX entries to validate
            sources: Dict of source_name -> ValidationSource. If None, all defaults used.
        """
        self.entries = entries
        self.sources = sources or build_sources(DEFAULT_ORDER)
        self.results = {
            "validated": [],
            "mismatches": [],
            "not_found": [],
        }

    def validate_all(self):
        """Validate all entries against sources"""
        if not self.entries:
            print("⚠ No entries to validate")
            return

        print("🔍 Starting validation against multiple sources...")
        print(f"⏱ Estimated time: ~{len(self.entries) * 3 // 60} minutes")
        print()

        total = len(self.entries)
        for idx, entry in enumerate(self.entries, 1):
            entry_id = entry.get("ID", "unknown")
            title = entry.get("title", "No title")[:50]

            print(f"[{idx}/{total}] {entry_id}: {title}...", end=" ")

            result = self.validate_entry(entry)

            if result["status"] == "validated":
                print("✓")
                self.results["validated"].append(result)
            elif result["status"] == "mismatch":
                print("⚠")
                for issue in result["issues"]:
                    print(f"    {issue}")
                self.results["mismatches"].append(result)
            elif result["status"] == "not_found":
                print("✗")
                self.results["not_found"].append(result)

            # Delay between entries
            time.sleep(2)

    def validate_entry(self, entry: Dict) -> Dict:
        """Validate single entry against all sources in order"""
        result = {
            "id": entry.get("ID", "unknown"),
            "title": entry.get("title", ""),
            "status": "unknown",
            "issues": [],
            "corrected_fields": {},
            "search_method": None,
            "matches": {},
            "attempts": {},
        }

        # Try each source in order
        for source_name in DEFAULT_ORDER:
            if source_name not in self.sources:
                continue

            source = self.sources[source_name]

            # Check if source wants to attempt this entry
            can_try, why = source.should_attempt(entry)
            result["attempts"][source_name] = {
                "attempted": bool(can_try),
                "reason": why,
            }

            if not can_try:
                continue

            found = None
            search_method = None

            # Try DOI first if available
            doi = entry.get("doi")
            if doi:
                found = source.search_by_doi(doi)
                if found:
                    search_method = f"{source_name}:DOI"

            # Try title if DOI search didn't work
            if not found:
                title = entry.get("title")
                if title:
                    found = source.search_by_title(title)
                    if found:
                        search_method = f"{source_name}:Title"

            if found:
                corrected_fields = source.extract_bibtex_fields(found)
                result["matches"][source_name] = {
                    "search_method": search_method,
                    "source_data": found,
                    "corrected_fields": corrected_fields,
                }

            # Rate limiting between sources
            time.sleep(1)

        # Determine status and collect issues
        if not result["matches"]:
            result["status"] = "not_found"
            result["issues"].append("Entry not found in any source")
            return result

        # Choose corrected fields with priority: dblp -> scholar -> semantic
        for preferred in DEFAULT_ORDER:
            if preferred in result["matches"]:
                result["corrected_fields"] = result["matches"][preferred][
                    "corrected_fields"
                ]
                result["search_method"] = result["matches"][preferred]["search_method"]
                break

        # Compare against each source match; prefix issues with source name
        issues = []
        for source_name, m in result["matches"].items():
            source_issues = self.compare_with_corrected(entry, m["corrected_fields"])
            issues.extend([f"{source_name.upper()}: {i}" for i in source_issues])

        # De-duplicate issues while preserving order
        issues = list(dict.fromkeys(issues))

        result["issues"] = issues
        result["status"] = "validated" if not issues else "mismatch"
        return result

    def normalize_string(self, s: str) -> str:
        """Normalize string for comparison"""
        import re

        if not s:
            return ""
        s = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", s)
        s = re.sub(r"[{}]", "", s)
        s = re.sub(r"[^\w\s]", " ", s.lower())
        s = " ".join(s.split())
        return s

    def similarity(self, a: str, b: str) -> float:
        """Calculate similarity ratio"""
        return SequenceMatcher(
            None, self.normalize_string(a), self.normalize_string(b)
        ).ratio()

    def compare_with_corrected(self, original: Dict, corrected: Dict) -> List[str]:
        """Compare original entry with corrected fields from validation source"""
        issues = []

        # Compare authors
        if "author" in corrected and "author" in original:
            orig_authors = authors_to_list(original.get("author"))
            corr_authors = authors_to_list(corrected.get("author"))

            if len(orig_authors) != len(corr_authors):
                issues.append(
                    f"AUTHORS: Different count ({len(orig_authors)} vs {len(corr_authors)})"
                )
            else:
                mismatches = []
                for oa, ca in zip(orig_authors, corr_authors):
                    if self.similarity(oa, ca) < 0.75:
                        mismatches.append(f"'{oa}' vs '{ca}'")
                if mismatches and len(mismatches) <= 3:
                    issues.append(f"AUTHORS: {'; '.join(mismatches)}")

        # Compare venue
        orig_venue = original.get("booktitle") or original.get("journal") or ""
        corr_venue = corrected.get("venue", "")
        if orig_venue and corr_venue:
            if self.similarity(orig_venue, corr_venue) < 0.6:
                issues.append(f"VENUE: '{orig_venue}' vs '{corr_venue}'")

        # Compare year
        orig_year = original.get("year", "")
        corr_year = corrected.get("year", "")
        if orig_year and corr_year and orig_year != corr_year:
            issues.append(f"YEAR: {orig_year} vs {corr_year}")

        # Compare title
        if "title" in original and "title" in corrected:
            sim = self.similarity(original["title"], corrected["title"])
            if sim < 0.85:
                issues.append(f"TITLE: Low similarity ({sim:.2f})")

        return issues

    def apply_corrections_to_entries(self) -> List[Dict]:
        """Apply corrected fields to entries and return updated copy"""
        updated_entries = []

        for entry in self.entries:
            entry_copy = deepcopy(entry)
            entry_id = entry.get("ID")

            # Find validation result for this entry
            validation_result = None
            for result in self.results["validated"] + self.results["mismatches"]:
                if result["id"] == entry_id:
                    validation_result = result
                    break

            # Apply corrections if validated or has mismatches
            if validation_result and "corrected_fields" in validation_result:
                corrected = validation_result["corrected_fields"]
                for field, value in corrected.items():
                    if value:  # Only update non-empty values
                        # Map 'venue' to appropriate BibTeX field
                        if field == "venue":
                            if entry.get("ENTRYTYPE") == "article":
                                entry_copy["journal"] = value
                            else:
                                entry_copy["booktitle"] = value
                        else:
                            entry_copy[field] = value

                # Add validation note
                source_info = validation_result.get("search_method", "unknown")
                existing_note = entry_copy.get("note", "")
                validation_note = f"Validated via {source_info}"
                entry_copy["note"] = (
                    f"{existing_note}; {validation_note}"
                    if existing_note
                    else validation_note
                )

            updated_entries.append(entry_copy)

        return updated_entries
