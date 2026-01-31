"""Interactive UI for candidate selection using questionary."""

from __future__ import annotations

from typing import Dict, List, Optional

import questionary


IMPORTANT_FIELDS_ORDER = [
    "title",
    "author",
    "year",
    "doi",
    "url",
    "journal",
    "booktitle",
    "venue",
    "publisher",
    "volume",
    "number",
    "pages",
]


def _format_entry_compact(entry: Dict) -> str:
    """Format entry as a single-line compact summary."""
    title = (entry.get("title") or "").strip().replace("\n", " ")
    year = (entry.get("year") or "").strip()
    author = (entry.get("author") or "").strip().replace("\n", " ")
    doi = (entry.get("doi") or "").strip()

    if len(author) > 80:
        author = author[:77] + "..."
    if len(title) > 90:
        title = title[:87] + "..."

    parts: List[str] = []
    if year:
        parts.append(year)
    if author:
        parts.append(author)
    if title:
        parts.append(title)
    if doi:
        parts.append(f"doi:{doi}")
    return " | ".join(parts) if parts else "(no metadata)"


def _format_entry_full(entry: Dict, header: str) -> str:
    """Format entry as multi-line detailed view."""
    lines: List[str] = [header]

    def add(k: str, v: Optional[str]) -> None:
        v = (v or "").strip()
        if not v:
            return
        lines.append(f"{k}: {v}")

    for k in IMPORTANT_FIELDS_ORDER:
        if entry.get(k):
            add(k, str(entry.get(k)))

    for k in sorted(entry.keys()):
        if k in IMPORTANT_FIELDS_ORDER or k in ("ENTRYTYPE", "ID"):
            continue
        v = entry.get(k)
        if v in (None, "", []):
            continue
        add(k, str(v))

    return "\n".join(lines)


def choose_candidate_interactive(
    original_entry: Dict,
    candidates: List[Dict],
    *,
    entry_id: str,
) -> Optional[int]:
    """
    Interactive candidate selector using arrow keys.
    
    Args:
        original_entry: The original BibTeX entry dict
        candidates: List of candidate dicts with keys: source, search_method, corrected_fields, score, components
        entry_id: Entry ID for display purposes
        
    Returns:
        Selected candidate index (0-based), or None if user chose to skip
    """
    if not candidates:
        return None

    choices: List[questionary.Choice] = []
    for i, c in enumerate(candidates):
        cf = c.get("corrected_fields") or {}
        score = c.get("score", 0.0)
        src = c.get("source", "unknown")
        method = c.get("search_method", "unknown")
        label = f"[{i+1}] score={score:.2f} {src} via {method} :: {_format_entry_compact(cf)}"
        choices.append(questionary.Choice(title=label, value=i))

    choices.append(questionary.Choice(title="[0] Skip (do not apply a match)", value=None))

    while True:
        sel = questionary.select(
            f"Ambiguous match for {entry_id}. Choose a candidate (or skip):",
            choices=choices,
            use_shortcuts=False,
        ).ask()

        # questionary may return:
        #  - int index (our desired value)
        #  - None (skip)
        #  - str shortcut key (e.g. "0" or "1") depending on questionary/terminal behavior
        if sel is None:
            return None
        if isinstance(sel, str):
            s = sel.strip().lower()
            if s in ("0", "s", "skip"):
                return None
            if s.isdigit():
                n = int(s)
                if n == 0:
                    return None
                sel = n - 1
            else:
                # Unknown string => treat as skip to be safe
                return None

        # Defensive: ensure integer index is in range
        if not isinstance(sel, int) or not (0 <= sel < len(candidates)):
            return None

        chosen = candidates[sel]
        cf = chosen.get("corrected_fields") or {}

        print("\n" + "-" * 100)
        print(_format_entry_full(original_entry, header=f"ORIGINAL ENTRY ({entry_id})"))
        print("-" * 100)
        print(
            _format_entry_full(
                cf,
                header=(
                    "PROPOSED MATCH "
                    f"(score={chosen.get('score', 0.0):.2f}, "
                    f"source={chosen.get('source')}, via={chosen.get('search_method')})"
                ),
            )
        )
        print("-" * 100 + "\n")

        ok = questionary.confirm("Accept this match?", default=True).ask()
        if ok:
            return sel
