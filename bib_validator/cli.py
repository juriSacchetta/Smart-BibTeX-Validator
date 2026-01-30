"""Command-line interface"""

import argparse
import sys
from .logging_utils import setup_logging
from .bibtex_io import load_bibtex_entries, write_bibtex_entries
from .validator import SmartBibtexValidator
from .reporting import generate_report, print_summary
from .sources import build_sources
from .sanitize import sanitize_entries


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Smart BibTeX validator - checks entries against multiple academic sources"
    )
    parser.add_argument("bibtex_file", help="BibTeX file to validate")
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=["crossref", "dblp", "semantic"],
        default=["crossref", "dblp", "semantic"],
        help="Validation sources to use (default: all)",
    )
    parser.add_argument(
        "--output-bib",
        default="bibliography_updated.bib",
        help="Output file for updated bibliography (default: bibliography_updated.bib)",
    )
    parser.add_argument(
        "--output-report",
        default="validation_report.txt",
        help="Output file for validation report (default: validation_report.txt)",
    )
    parser.add_argument(
        "--source-delay",
        type=float,
        default=0.1,
        help="Delay (seconds) between source queries within an entry (default: 0.1, set to 0 for fastest)",
    )
    parser.add_argument(
        "--entry-delay",
        type=float,
        default=0.0,
        help="Delay (seconds) between entries (default: 0.0)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=3,
        help="Max parallel threads for querying sources per entry (default: 3, ignored with --no-stop-on-first-match)",
    )
    parser.add_argument(
        "--entry-timeout",
        type=float,
        default=15.0,
        help="Timeout (seconds) for all source queries on a single entry (default: 15.0)",
    )
    parser.add_argument(
        "--no-stop-on-first-match",
        dest="stop_on_first_match",
        action="store_false",
        default=True,
        help="Query all sources in parallel instead of preferred order (legacy behavior)",
    )
    parser.add_argument(
        "--fail-on-lint",
        action="store_true",
        help="Exit with non-zero code if lint issues are found (still writes report unless --no-report-on-fail)",
    )
    parser.add_argument(
        "--no-write-on-lint",
        action="store_true",
        help="Do not write the updated BibTeX file if lint issues are found",
    )
    parser.add_argument(
        "--apply-corrections",
        choices=["preferred", "validated-only", "never"],
        default="preferred",
        help="How to apply corrections to the output bib (default: preferred)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Initialize logging
    setup_logging(args.debug)

    # Load entries
    print(f"📖 Parsing BibTeX file: {args.bibtex_file}")
    try:
        entries = load_bibtex_entries(args.bibtex_file)
        print(f"✓ Found {len(entries)} entries\n")
    except Exception as e:
        print(f"❌ Error parsing BibTeX file: {e}")
        sys.exit(1)

    # Build sources
    sources = build_sources(args.sources)
    if not sources:
        print("❌ No validation sources selected")
        sys.exit(1)

    print(f"📚 Validation sources: {', '.join(sources.keys())}\n")

    # Run validation
    validator = SmartBibtexValidator(
        entries,
        sources,
        source_delay=args.source_delay,
        entry_delay=args.entry_delay,
        max_workers=args.max_workers,
        entry_timeout=args.entry_timeout,
        stop_on_first_match=args.stop_on_first_match,
    )
    validator.validate_all()

    # Generate report
    generate_report(entries, validator.results, args.sources, validator.lint_results, args.output_report)
    print_summary(entries, validator.results, len(entries))

    # Pipeline: optionally stop/skip based on lint results
    bib_written = False
    if validator.lint_issue_count > 0 and args.no_write_on_lint:
        print(f"⚠ Lint issues found ({validator.lint_issue_count}). Skipping BibTeX output due to --no-write-on-lint.")
    else:
        updated_entries = validator.apply_corrections_to_entries(
            mode=args.apply_corrections,
        )
        # Sanitize entries before writing: remove empty fields, normalize whitespace, clean years
        sanitized_entries = sanitize_entries(updated_entries)
        write_bibtex_entries(args.output_bib, sanitized_entries)
        print(f"✓ Updated BibTeX saved to {args.output_bib}")
        bib_written = True

    # Final message, conditional on whether bib was written
    completion_msg = f"✓ Validation complete! Check '{args.output_report}' for details"
    if bib_written:
        completion_msg += f" and '{args.output_bib}' for corrected entries"
    completion_msg += "."
    print(f"\n{completion_msg}")

    # Enforce --fail-on-lint exit code
    if args.fail_on_lint and validator.lint_issue_count > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
