"""Reporting and summary functions"""

from typing import Dict, List


def generate_report(
    entries: List[Dict],
    results: Dict,
    sources: List[str],
    output_file: str = "validation_report.txt",
) -> None:
    """Generate detailed validation report"""
    print(f"\n📊 Generating report: {output_file}")

    total_checked = len(entries)
    validated = len(results["validated"])
    mismatches = len(results["mismatches"])
    not_found = len(results["not_found"])

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("Smart BibTeX Validation Report\n")
        f.write("=" * 80 + "\n\n")

        f.write(f"Total entries in file: {len(entries)}\n")
        f.write(f"Entries validated: {total_checked}\n")
        f.write(f"Validation sources: {', '.join(sources)}\n\n")

        f.write(f"✓ Validated: {validated}\n")
        f.write(f"⚠ Mismatches: {mismatches}\n")
        f.write(f"✗ Not found: {not_found}\n\n")

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
                f.write("Issues:\n")
                for issue in result["issues"]:
                    f.write(f"  - {issue}\n")
                f.write("\n")

        # Not found
        if results["not_found"]:
            f.write("=" * 80 + "\n")
            f.write("ENTRIES NOT FOUND IN ANY SOURCE\n")
            f.write("=" * 80 + "\n\n")

            for result in results["not_found"]:
                f.write(f"{result['id']}: {result['title'][:60]}...\n")

        # Validated
        if results["validated"]:
            f.write("\n" + "=" * 80 + "\n")
            f.write("VALIDATED ENTRIES\n")
            f.write("=" * 80 + "\n\n")
            for result in results["validated"]:
                f.write(f"✓ {result['id']}\n")
        
        # URL checks
        if results.get("url_checks"):
            f.write("\n" + "=" * 80 + "\n")
            f.write("URL REACHABILITY CHECKS\n")
            f.write("=" * 80 + "\n\n")
            
            unreachable = [r for r in results["url_checks"] if not r["reachable"]]
            reachable = [r for r in results["url_checks"] if r["reachable"]]
            
            f.write(f"Total URLs checked: {len(results['url_checks'])}\n")
            f.write(f"✓ Reachable: {len(reachable)}\n")
            f.write(f"✗ Unreachable: {len(unreachable)}\n\n")
            
            if unreachable:
                f.write("Unreachable URLs:\n")
                for result in unreachable:
                    f.write(f"  {result['id']}: {result['url']}\n")
                    f.write(f"    Error: {result['detail']}\n")

    print(f"✓ Report saved to {output_file}")


def print_summary(entries: List[Dict], results: Dict, checked: int) -> None:
    """Print validation summary to console"""
    validated = len(results["validated"])
    mismatches = len(results["mismatches"])
    not_found = len(results["not_found"])

    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    print(f"Total entries:     {len(entries)}")
    print(f"Checked:           {checked}")
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
    print("=" * 80)

    if mismatches > 0:
        print(f"\n⚠ {mismatches} entries need manual review")
    if not_found > 0:
        print(f"⚠ {not_found} entries not found in any source")
