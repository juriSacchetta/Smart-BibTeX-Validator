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
    """Write entries to a BibTeX file"""
    db = bibtexparser.bibdatabase.BibDatabase()
    db.entries = entries
    
    with open(path, "w", encoding="utf-8") as bibfile:
        bibtexparser.dump(db, bibfile)
