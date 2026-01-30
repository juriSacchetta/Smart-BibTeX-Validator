"""BibTeX file I/O operations"""

import bibtexparser
import bibtexparser.bibdatabase
from typing import List, Dict


def load_bibtex_entries(path: str) -> List[Dict]:
    """Load entries from a BibTeX file"""
    with open(path, "r", encoding="utf-8") as bibfile:
        parser = bibtexparser.bparser.BibTexParser(common_strings=True)
        parser.ignore_nonstandard_types = False
        parser.homogenize_fields = False
        bib_database = bibtexparser.load(bibfile, parser=parser)
        return bib_database.entries


def write_bibtex_entries(path: str, entries: List[Dict]) -> None:
    """
    Write entries to a BibTeX file.
    
    Validates that all entries have required ID and ENTRYTYPE fields before writing.
    Raises ValueError if any entry is missing these fields.
    
    Args:
        path: Output file path
        entries: List of BibTeX entry dicts
    
    Raises:
        ValueError: If any entry is missing ID or ENTRYTYPE
    """
    # Defensive validation: ensure all entries have required keys
    for i, entry in enumerate(entries):
        if "ID" not in entry or "ENTRYTYPE" not in entry:
            raise ValueError(
                f"Cannot write entry at index {i}: missing required keys. "
                f"ID present: {'ID' in entry}, ENTRYTYPE present: {'ENTRYTYPE' in entry}. "
                f"Entry keys: {sorted(entry.keys())}"
            )
    
    db = bibtexparser.bibdatabase.BibDatabase()
    db.entries = entries
    
    with open(path, "w", encoding="utf-8") as bibfile:
        bibtexparser.dump(db, bibfile)
