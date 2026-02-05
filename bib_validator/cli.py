"""Command-line interface"""

import argparse
import sys
from .bibtex_io import load_bibtex_entries, write_bibtex_entries
from .validator import SmartBibtexValidator
from .reporting import generate_report, print_summary
from .sources import build_sources


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Smart BibTeX validator - checks entries against multiple academic sources"
    )
    parser.add_argument("bibtex_file", help="BibTeX file to validate")
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=["dblp", "scholar", "semantic"],
        default=["dblp", "scholar", "semantic"],
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
        "--skip-url-check",
        action="store_true",
        help="Skip URL reachability checks",
    )

    args = parser.parse_args()

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

    # Run validation
    validator = SmartBibtexValidator(entries, sources)
    validator.validate_all(check_urls=not args.skip_url_check)

    # Generate outputs
    updated_entries = validator.apply_corrections_to_entries()
    write_bibtex_entries(args.output_bib, updated_entries)
    print(f"✓ Updated BibTeX saved to {args.output_bib}")

    generate_report(entries, validator.results, args.sources, args.output_report)
    print_summary(entries, validator.results, len(entries))

    print(
        f"\n✓ Validation complete! Check '{args.output_report}' for details and '{args.output_bib}' for corrected entries."
    )


if __name__ == "__main__":
    main()
