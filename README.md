# BibTeX Validator (multi-source)

Validate BibTeX entries against multiple metadata sources (DBLP, Google Scholar, Semantic Scholar) and generate a report highlighting mismatches.

This repository uses a small Python package (`bib_validator/`) plus a thin wrapper script (`bib.py`).

---

## Features

- Multi-source validation:
  - **DBLP**
  - **Google Scholar**
  - **Semantic Scholar**
- For **each input entry**, the validator will iterate through **all enabled sources** (DBLP → Scholar → Semantic Scholar).
- Any decision to skip an entry is **per-source** via `should_attempt()` (there is no global "filter entries first" step).
- Search strategy per source:
  1. DOI (when supported by the source)
  2. Title (fallback)
- Compares common fields (as available):
  - title, authors, venue (journal/booktitle), year
- Outputs:
  - `validation_report.txt` (detailed report)
  - `bibliography_updated.bib` (updated BibTeX with applied corrections)

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Usage

### Validate using all sources (default)

```bash
python3 bib.py bibliography.bib
```

### Select sources

```bash
python3 bib.py bibliography.bib --sources dblp scholar semantic
```

### Choose output filenames

```bash
python3 bib.py bibliography.bib \
  --output-bib bibliography_updated.bib \
  --output-report validation_report.txt
```

---

## Output

### `validation_report.txt`
Includes:
- **Entries with mismatches** (issues are prefixed per source, e.g. `DBLP: ...`, `SCHOLAR: ...`)
- **Entries not found in any source**
- **Validated entries**

### `bibliography_updated.bib`
A copy of the input bibliography where the tool applies the selected "preferred" corrections:
- Preferred source order is: **DBLP → Scholar → Semantic Scholar**
- Venue updates are mapped to:
  - `journal` for `@article`
  - `booktitle` otherwise
- A `note` field is added/updated with `Validated via <source:method>`

---

## How it works (high-level)

For each BibTeX entry:
1. For each enabled source (DBLP → Scholar → Semantic Scholar):
   - The source decides whether to attempt validation (`should_attempt(entry)`).
   - If attempted, the validator tries DOI lookup first (if implemented), then title lookup.
   - Any match is stored under that source in the result.
2. If at least one source matched, the validator compares your entry against each matched source result.
3. The tool chooses a "preferred" source (DBLP first, then Scholar, then Semantic Scholar) to populate `bibliography_updated.bib`.

---

## Project structure

- `bib.py` — wrapper entrypoint
- `bib_validator/cli.py` — CLI / orchestration
- `bib_validator/validator.py` — core validation logic
- `bib_validator/bibtex_io.py` — BibTeX I/O helpers
- `bib_validator/reporting.py` — report + console summary
- `bib_validator/sources/` — validation sources
  - `base.py` — common interface (`ValidationSource`)
  - `dblp.py` — DBLP implementation
  - `scholar.py` — Google Scholar implementation
  - `semantic.py` — Semantic Scholar implementation

---

## Notes / Tips

- Adding DOIs improves accuracy and speed.
- "Not found" does not necessarily mean the entry is wrong:
  - DBLP focuses on CS and may not list all venues
  - Some entries are web resources and may be skipped by DBLP
  - Very recent papers can take time to appear in some databases
- The tool degrades gracefully if optional libraries are missing:
  - If `scholarly` is not installed, Google Scholar source will be skipped
  - If `semanticscholar` is not installed, Semantic Scholar source will be skipped

---

## Troubleshooting

### Google Scholar returns nothing
`scholarly` can be rate-limited or blocked depending on IP/network conditions. Try again later or rely on the other sources.

### Rate limiting (DBLP / remote services)
DBLP requests are rate limited; the source includes retry/backoff, and the validator includes delays between requests.
If you still hit limits, increase delays in the validator/source implementation.
