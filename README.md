# BibTeX Validator (multi-source)

Validate BibTeX entries against multiple metadata sources (Crossref, DBLP, Semantic Scholar) and generate a report highlighting mismatches.

This repository uses a small Python package (`bib_validator/`) plus a thin wrapper script (`bib.py`).

---

## Features

- **Lint stage** (local, no network):
  - Detects common BibTeX quality issues (missing fields, format problems, etc.)
  - Issues are reported in the validation report
  - Lint issues are **advisory**: if a remote match is found and is clean, missing fields are auto-filled and lint warnings are removed from the report
- **Trustworthy by default** (safe auto-corrections):
  - DOI-based matches are always trusted (deterministic, authoritative)
  - Title-based matches are only used for destructive overwrites if similarity is high (≥ 75%)
  - Low-confidence title matches still fill missing non-destructive fields (author, venue)
  - This means: the tool maximizes completion while minimizing the risk of corrupting correct data
- **Auto-fix strategy** (minimize manual work):
  - DOI-based matches: overwrite DOI, year, title (authoritative)
  - High-confidence title matches (≥ 75% similarity): overwrite DOI, year, title
  - Low-confidence title matches: fill missing author/venue only (non-destructive)
  - Always fill missing required fields if any source provides them
  - Author and venue fields are filled if missing (never overwritten if present)
  - All changes are recorded in `autofilled_fields` in the report
- Multi-source validation:
  - **Crossref** (primary source for DOI/title metadata)
  - **DBLP** (CS and conference proceedings)
  - **Semantic Scholar** (fallback, general coverage)
- For **each input entry**, the validator queries sources in **preferred order** (Crossref → DBLP → Semantic Scholar) and **stops at the first match** (default behavior, can be disabled with `--no-stop-on-first-match`).
- Search strategy per source:
  1. DOI (when supported by the source)
  2. Title (fallback)
- Compares common fields (as available):
  - title (similarity-based), authors (last-name overlap), venue (journal/booktitle), year
- Outputs:
  - `validation_report.txt` (detailed report including lint issues, auto-filled fields, and true mismatches)
  - `bibliography_updated.bib` (updated BibTeX with applied corrections and `x-found-in` provenance field)

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
python3 bib.py bibliography.bib --sources crossref dblp semantic
```

### Choose output filenames

```bash
python3 bib.py bibliography.bib \
  --output-bib bibliography_updated.bib \
  --output-report validation_report.txt
```

### Use parallel query mode (legacy behavior)

By default, sources are queried in preferred order and validation stops at the first match. To query all sources in parallel instead:

```bash
python3 bib.py bibliography.bib --no-stop-on-first-match
```

---

## Output

### `validation_report.txt`
Includes:
- **Lint issues** (local quality checks):
  - Missing required fields
  - Format problems (DOI, URL, year, author)
  - Venue field conflicts
  - Note: Many lint issues are auto-fixed when a remote source provides the missing data
- **Entries with mismatches** (issues are prefixed per source, e.g. `CROSSREF: ...`, `DBLP: ...`, `LINT: ...`):
  - **True disagreements** with remote sources where:
    - Title similarity is below threshold (< 75%) when matched by title
    - Year conflicts with authoritative source
    - Venue similarity is low (< 60%)
    - Author last-name overlap is low (< 60%)
  - Auto-filled fields are recorded (so you can see what was fixed)
  - Details of which sources were queried (attempted, skipped, timed out, etc.)
  - **Important**: mismatches can still have non-destructive corrections applied (see "Auto-fill strategy" below)
- **Entries not found in any source**:
  - Shows which sources were queried/skipped and why
  - Note: "Not found" does not necessarily mean the entry is wrong
- **Validated entries** (zero remote mismatches; lint issues auto-fixed)
  - Auto-filled fields are recorded for transparency

---

### `bibliography_updated.bib`
A copy of the input bibliography with auto-applied corrections:
- **Auto-fill strategy** (trustworthy by default):
  - **DOI-based matches** (`x-found-in: *:DOI`):
    - Always overwrite: DOI, year, title (authoritative, deterministic)
  - **High-confidence title matches** (title similarity ≥ 75%):
    - Always overwrite: DOI, year, title
  - **Low-confidence title matches** (title similarity < 75%):
    - Only fill: author, venue/journal/booktitle, other missing fields (non-destructive)
    - Never overwrite: DOI, year, title (prevents corruption of correct data)
  - **Always fill if missing** (low risk):
    - DOI, year, title, author, venue — when not present in the original entry
- Preferred source order is: **Crossref → DBLP → Semantic Scholar**
- When a match is found (in default mode), only that source's corrections are applied
- Venue updates are mapped to:
  - `journal` for `@article`
  - `booktitle` otherwise
- An `x-found-in` field is added with the search method (e.g., `crossref:DOI` or `dblp:Title`)
- All entries are sanitized:
  - Empty fields removed
  - Whitespace normalized
  - DOIs canonicalized
  - URLs and DOIs preserved as-is (not LaTeX-encoded)
  - Text fields (title, author, etc.) converted to LaTeX-safe representation

---

## Trust model

The tool is **trustworthy by default** because it distinguishes between destructive and non-destructive corrections:

### What's always safe (trusted)
- DOI-based matches: DOI lookups are deterministic and authoritative
- Filling missing fields: no risk of corruption
- Non-destructive updates (author, venue) when already present: checked for low risk before applying

### What's guarded (title-based matches)
- If a title match has **high similarity** (≥ 75%): treat as trusted and allow DOI/year/title overwrites
- If a title match has **low similarity** (< 75%): only fill missing fields, never overwrite existing DOI/year/title

This means:
- **Maximum usefulness**: the tool completes incomplete entries aggressively
- **Minimum risk**: destructive updates only happen when the tool is confident
- **Manual review**: low-confidence mismatches are flagged in the report so you can decide

---

## How it works (high-level)

1. **Lint stage** (local, no network):
   - The tool runs local BibTeX quality checks on all entries.
   - Common issues: missing fields, malformed DOI/URL, suspicious authors, conflicting venue fields.
   - Results are included in the report for transparency.

2. **Remote validation** (default: preferred-order with early stopping):
   - For each entry, the validator queries enabled sources in preferred order (Crossref → DBLP → Semantic Scholar).
   - For each source: decide whether to attempt validation (`should_attempt(entry)`).
     - If skipped, reason is recorded (e.g., "missing title", "online entry without DOI").
   - If attempted, try DOI lookup first (if supported), then title lookup.
   - **As soon as a match is found**, validation stops and uses **only that source's data** for comparison.
   - Match confidence is computed:
     - DOI-based: confidence = 1.0 (deterministic, trusted)
     - Title-based: confidence = title similarity (0.0 to 1.0)
   - If a match passes the basic sanity checks, it is stored and compared against the original entry.
   - If no match is found after all sources, entry is classified as "not found" (or "mismatch" if lint issues exist).

3. **Smart status classification** (auto-fix focused):
   - **Validated**: zero remote mismatches (even if lint had issues—they're auto-fixed)
   - **Mismatch**: has true disagreements with remote sources (title low similarity, year conflict, venue mismatch, author last-name overlap mismatch)
   - **Not found**: no remote sources matched (unless blocking lint exists, then classified as mismatch for triage)
   - Lint issues that are resolved by remote corrections are removed from the report

4. **Auto-apply corrections** (trustworthy by default):
   - Apply matched source's data to `bibliography_updated.bib`
   - **For DOI-based matches**: always overwrite DOI, year, title (authoritative)
   - **For high-confidence title matches**: always overwrite DOI, year, title (high similarity)
   - **For low-confidence title matches**: only fill missing fields (non-destructive)
   - Record which fields were auto-filled per entry in the report
   - An `x-found-in` field records provenance (e.g., `crossref:DOI`)

---

## Comparison thresholds

The validator uses fuzzy string matching (via `rapidfuzz`) to compare fields:

- **Title similarity** (default 75%): if below threshold for a title-based match, entry is flagged as mismatch, and destructive field overwrites are skipped
- **Venue similarity** (default 60%): if below threshold, flagged as mismatch
- **Author last-name overlap** (default 60%): if below threshold, flagged as mismatch (prevents false positives from simple author count checks)
- **Year**: exact match required (if present in both)

These thresholds are defined as constants in `bib_validator/validator.py`.

---

## Project structure

- `bib.py` — wrapper entrypoint
- `bib_validator/cli.py` — CLI / orchestration
- `bib_validator/validator.py` — core validation logic with trust guards
- `bib_validator/lint.py` — local BibTeX quality checks
- `bib_validator/bibtex_io.py` — BibTeX I/O helpers
- `bib_validator/reporting.py` — report + console summary
- `bib_validator/normalize.py` — field normalization and sanitization
- `bib_validator/sanitize.py` — entry sanitization for clean output
- `bib_validator/sources/` — validation sources
  - `base.py` — common interface (`ValidationSource`)
  - `crossref.py` — Crossref API implementation (primary DOI/title metadata source)
  - `dblp.py` — DBLP implementation (CS conference proceedings)
  - `semantic.py` — Semantic Scholar implementation (uses REST API, fallback general coverage)

---

## Notes / Tips

- Adding DOIs improves accuracy and speed (Crossref DOI lookup is authoritative and enables destructive updates).
- "Not found" does not necessarily mean the entry is wrong:
  - DBLP focuses on CS and may not list all venues
  - Some entries are web resources and may be skipped by DBLP
  - Very recent papers can take time to appear in some databases
  - A source may be rate-limited or time out on your network
- The tool degrades gracefully if sources are unavailable or time out:
  - Crossref uses polite rate limiting and includes a user-agent (requests ~0.1s apart)
  - DBLP includes exponential backoff retry on rate limits (429)
  - Semantic Scholar rate-limits and cools down gracefully
- **Mismatches now indicate true disagreements** with sources (not just missing lint fields)—much easier to triage.
- Use `--no-write-on-lint` to prevent updating the bibliography when lint issues are found; use `--fail-on-lint` to fail the build in CI if any lint issues are detected.
- Use `--apply-corrections validated-only` to apply corrections only to entries that passed validation (most conservative, safest for critical bibliographies).
- Use `--no-stop-on-first-match` to revert to parallel querying mode (queries all sources concurrently instead of stopping at the first match).
- The `x-found-in` field records provenance (e.g., `crossref:DOI`, `dblp:Title`). This field is optional and can be ignored by LaTeX/BibTeX tools.
- Auto-filled fields are recorded per entry in the report (`autofilled_fields`) so you can immediately see what was fixed without opening the bib file.
- **Trustworthiness guarantees**: 
  - DOI-based matches are always trusted (deterministic, authoritative)
  - High-similarity title matches (≥ 75%) are treated as trusted
  - Low-similarity title matches still fill missing fields but never overwrite existing data
  - This minimizes risk while maximizing completion

---

## Troubleshooting

### No matches found
- Ensure your entries have DOI or title fields.
- Try adding a DOI to entries; Crossref lookups are faster and more reliable, and enable trusted updates.
- Check the `validation_report.txt` to see which sources attempted the query and why (if any were skipped).

### Rate limiting (Crossref / DBLP)
- Crossref has polite rate limiting (enforced at ~0.1s between requests) and includes a `User-Agent` header per Crossref best practices.
- DBLP includes automatic exponential backoff on rate limits.
- If you still hit limits, increase `--source-delay` (default 0.1s) or `--entry-delay` (default 0.0s).

### Source queries timing out
If you see "TIMEOUT" in the report for a source, increase `--entry-timeout` (default 15s).
If a particular source is slow, increase `--source-delay` (delay between individual source queries within an entry).

### Why was my DOI not overwritten by a low-confidence title match?
The tool is conservative with destructive updates. If a title-based match has low similarity (< 75%), it fills missing fields but does not overwrite existing DOI or year. This is intentional—to prevent corruption of correct data based on uncertain matches. If you want more aggressive updates, use `--apply-corrections preferred` and review the report; or add a DOI to the entry so the next run uses a trusted DOI-based match.
