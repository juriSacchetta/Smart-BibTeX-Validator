"""Reporting and summary functions"""

from typing import Dict, List


def generate_report(
    entries: List[Dict],
    results: Dict,
    sources: List[str],
    lint_results: Dict = None,
    output_file: str = "validation_report.txt",
    dedupe_decisions=None,
) -> None:
    """Generate detailed validation report"""
    print(f"\n📊 Generating report: {output_file}")

    lint_results = lint_results or {}
    total_entries = len(entries)
    validated = len(results["validated"])
    mismatches = len(results["mismatches"])
    not_found = len(results["not_found"])
    lint_entries_count = len(lint_results)
    lint_issues_count = sum(len(v) for v in lint_results.values())

    # Build needs_review list across all buckets
    needs_review = []
    for bucket in ("mismatches", "not_found", "validated"):
        for r in results.get(bucket, []):
            if r.get("needs_review"):
                needs_review.append(r)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("Smart BibTeX Validation Report\n")
        f.write("=" * 80 + "\n\n")

        f.write(f"Total entries in file: {total_entries}\n")
        f.write(f"Entries processed: {total_entries}\n")
        f.write(f"Validation sources: {', '.join(sources)}\n\n")

        f.write(f"✓ Validated: {validated}\n")
        f.write(f"⚠ Mismatches: {mismatches}\n")
        f.write(f"✗ Not found: {not_found}\n")
        f.write(f"🧹 Lint issues:   {lint_issues_count} (across {lint_entries_count} entries)\n")
        f.write(f"🔍 Needs review:  {len(needs_review)}\n\n")

        # DEDUPLICATION section
        if dedupe_decisions:
            f.write("=" * 80 + "\n")
            f.write("DEDUPLICATION (Duplicate Entries Detected)\n")
            f.write("=" * 80 + "\n\n")
            total_dupes = sum(len(d.drop_ids) for d in dedupe_decisions)
            f.write(f"Duplicate entries that can be dropped (keep-first policy): {total_dupes}\n")
            f.write(f"Duplicate groups: {len(dedupe_decisions)}\n\n")

            for d in dedupe_decisions:
                f.write(f"KEEP: {d.keep_id} | reason={d.reason} | confidence={d.confidence:.2f}\n")
                for drop in d.drop_ids:
                    f.write(f"  DROP: {drop}\n")
                f.write("\n")

        # NEEDS HUMAN REVIEW section
        if needs_review:
            f.write("=" * 80 + "\n")
            f.write("NEEDS HUMAN REVIEW (Tool cannot guarantee maximum trust)\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"Total entries requiring review: {len(needs_review)}\n\n")

            for r in needs_review:
                f.write(f"Entry ID: {r['id']}\n")
                f.write(f"Title: {r.get('title', '')}\n")
                f.write(f"Status: {r.get('status', 'unknown')}\n")
                f.write(f"Found via: {r.get('search_method', 'none')}\n")
                if r.get("match_kind"):
                    f.write(f"Match kind: {r.get('match_kind')}\n")
                if r.get("match_confidence") is not None:
                    f.write(f"Match confidence: {r.get('match_confidence'):.2f}\n")
                if r.get("review_reasons"):
                    f.write("Review reasons:\n")
                    for reason in r["review_reasons"]:
                        f.write(f"  - {reason}\n")
                if r.get("rejected_candidates"):
                    f.write("Rejected candidate matches:\n")
                    for c in r["rejected_candidates"]:
                        src = c.get("source", "unknown")
                        method = c.get("search_method", "unknown")
                        reasons = "; ".join(c.get("reasons") or [])
                        f.write(f"  - {src} via {method}: {reasons}\n")
                if r.get("candidate_matches"):
                    f.write("Top candidate matches (ambiguous):\n")
                    for c in r["candidate_matches"]:
                        src = c.get("source", "unknown")
                        method = c.get("search_method", "unknown")
                        score = c.get("score", 0.0)
                        cf = c.get("corrected_fields") or {}
                        title = (cf.get("title") or "")[:80]
                        year = cf.get("year") or ""
                        doi = cf.get("doi") or ""
                        f.write(f"  - {score:.2f} {src} via {method} | {year} | {doi} | {title}\n")
                if r.get("autofilled_fields"):
                    f.write(f"Auto-filled fields: {', '.join(r['autofilled_fields'])}\n")
                f.write("\n")

        # Lint issues section
        if lint_results:
            f.write("=" * 80 + "\n")
            f.write("LINT ISSUES (Local BibTeX quality checks - many auto-fixed)\n")
            f.write("=" * 80 + "\n\n")
            for entry_id, issues in lint_results.items():
                title = ""
                for e in entries:
                    if e.get("ID") == entry_id:
                        title = e.get("title", "")
                        break
                f.write(f"{entry_id}: {title[:60]}...\n")
                for issue in issues:
                    f.write(f"  - {issue}\n")
                f.write("\n")

        # Mismatches
        if results["mismatches"]:
            f.write("=" * 80 + "\n")
            f.write("ENTRIES WITH MISMATCHES (Require Manual Review)\n")
            f.write("=" * 80 + "\n\n")

            for result in results["mismatches"]:
                f.write(f"Entry ID: {result['id']}\n")
                f.write(f"Title: {result['title']}\n")
                f.write(f"Found via: {result.get('search_method', 'unknown')}\n")
                if result.get("matches"):
                    f.write(f"Sources matched: {', '.join(result['matches'].keys())}\n")
                
                if result.get("autofilled_fields"):
                    f.write(f"Auto-filled fields: {', '.join(result['autofilled_fields'])}\n")
                
                f.write("Issues:\n")
                for issue in result["issues"]:
                    f.write(f"  - {issue}\n")
                
                # Add source attempts details
                if result.get("attempts"):
                    f.write("Source queries:\n")
                    for source_name, attempt_info in result["attempts"].items():
                        attempted = attempt_info.get("attempted", False)
                        reason = attempt_info.get("reason", "unknown")
                        error = attempt_info.get("error", None)
                        
                        if not attempted:
                            f.write(f"  - {source_name}: skipped ({reason})\n")
                        elif error:
                            f.write(f"  - {source_name}: attempted, {error}\n")
                        else:
                            f.write(f"  - {source_name}: attempted\n")
                
                f.write("\n")

        # Not found
        if results["not_found"]:
            f.write("=" * 80 + "\n")
            f.write("ENTRIES NOT FOUND IN ANY SOURCE\n")
            f.write("=" * 80 + "\n\n")

            for result in results["not_found"]:
                f.write(f"{result['id']}: {result['title'][:60]}...\n")
                
                # Add source attempts details for not-found entries too
                if result.get("attempts"):
                    f.write("  Source queries:\n")
                    for source_name, attempt_info in result["attempts"].items():
                        attempted = attempt_info.get("attempted", False)
                        reason = attempt_info.get("reason", "unknown")
                        error = attempt_info.get("error", None)
                        
                        if not attempted:
                            f.write(f"    - {source_name}: skipped ({reason})\n")
                        elif error:
                            f.write(f"    - {source_name}: attempted, {error}\n")
                        else:
                            f.write(f"    - {source_name}: attempted (no match)\n")
                
                f.write("\n")

    print(f"✓ Report saved to {output_file}")


def print_summary(entries: List[Dict], results: Dict, checked: int) -> None:
    """Print validation summary to console"""
    validated = len(results["validated"])
    mismatches = len(results["mismatches"])
    not_found = len(results["not_found"])
    
    # Compute needs_review count
    needs_review = sum(
        1 for bucket in ("validated", "mismatches", "not_found")
        for r in results.get(bucket, [])
        if r.get("needs_review")
    )

    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    print(f"Total entries:     {len(entries)}")
    print(f"Processed:         {checked}")
    print()
    print(
        f"✓ Validated:       {validated} ({validated / checked * 100 if checked else 0:.1f}%)"
    )
    print(
        f"⚠ Mismatches:      {mismatches} ({mismatches / checked * 100 if checked else 0:.1f}%)"
    )
    print(
        f"✗ Not found:       {not_found} ({not_found / checked * 100 if checked else 0:.1f}%)"
    )
    print(
        f"🔍 Needs review:   {needs_review} ({needs_review / checked * 100 if checked else 0:.1f}%)"
    )
    print("=" * 80)

    if needs_review > 0:
        print(f"\n⚠ {needs_review} entries need manual review. Check validation_report.txt for details.")
    if mismatches > 0:
        print(f"⚠ {mismatches} entries have true disagreements with sources")
    if not_found > 0:
        print(f"⚠ {not_found} entries not found in any source")
