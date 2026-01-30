#!/usr/bin/env python3
"""
Smart BibTeX Validator - Multi-source validation against DBLP, Google Scholar, and Semantic Scholar

Usage:
    python bib.py your_file.bib [--sources dblp scholar semantic]
"""

from bib_validator.cli import main

if __name__ == "__main__":
    main()
