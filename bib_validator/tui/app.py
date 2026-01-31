"""Interactive TUI for entry review and correction (two-pane)"""

import logging
from typing import Dict, List, Optional
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ListItem, ListView, Static, DataTable, Header, Footer
from textual.binding import Binding
from textual import events

from .state import EntryRow, UIState
from .logic import apply_candidate_to_entry
from ..validator import SmartBibtexValidator, TITLE_SIMILARITY_THRESHOLD
from ..lint import has_blocking_lint_issues, lint_entries


logger = logging.getLogger(__name__)


class _EntryItem(ListItem):
    """List item for a single entry"""

    def __init__(self, row: EntryRow):
        super().__init__()
        self.row = row

    def render(self) -> str:
        return self._label_text()

    def _label_text(self) -> str:
        status = {
            "unreviewed": " ",
            "edited": "*",
            "skipped": "S",
            "no_match": "N",
        }.get(self.row.status, "?")

        lint_flag = " "
        if self.row.lint_issues:
            lint_flag = "!" if has_blocking_lint_issues(self.row.lint_issues) else "~"

        title = (self.row.title or "").strip().replace("\n", " ")
        if len(title) > 55:
            title = title[:52] + "..."
        return f"[{status}{lint_flag}] {self.row.entry_id}: {title}"


class BibEditorTextualApp(App):
    """Two-pane editor: left entries list, right candidates list."""

    CSS = """
    #root { height: 100%; }
    #left { width: 40%; border: solid $primary; }
    #right { width: 60%; border: solid $primary; }
    #details { height: 18; border-top: solid $primary; }
    """

    BINDINGS = [
        Binding("enter", "apply_candidate", "Apply"),
        Binding("r", "revert_entry", "Revert"),
        Binding("s", "skip_entry", "Skip"),
        Binding("n", "no_match", "No match"),
        Binding("w", "write_and_quit", "Write & Quit"),
        Binding("q", "quit", "Quit"),
        Binding("tab", "focus_next", show=False),
        Binding("shift+tab", "focus_previous", show=False),
    ]

    def __init__(self, *, state: UIState, validator: SmartBibtexValidator) -> None:
        super().__init__()
        self.state = state
        self.validator = validator
        self._candidate_cache: Dict[str, List[Dict]] = {}
        self._candidate_cursor_by_entry: Dict[str, int] = {}
        self.edited_entries: Optional[List[Dict]] = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="root"):
            with Vertical(id="left"):
                yield Static("Entries")
                self.entry_list = ListView()
                yield self.entry_list
            with Vertical(id="right"):
                yield Static("Remote candidates")
                self.cand_table = DataTable(zebra_stripes=True)
                self.cand_table.can_focus = True
                yield self.cand_table
                self.details = Static("", id="details")
                yield self.details
        yield Footer()

    def on_mount(self) -> None:
        self._populate_entries()
        self._init_candidate_table()
        self.entry_list.index = self.state.selected_idx
        self._refresh_right()
        self.set_focus(self.entry_list)

    def _populate_entries(self) -> None:
        self.entry_list.clear()
        for row in self.state.rows:
            self.entry_list.append(_EntryItem(row))

    def _init_candidate_table(self) -> None:
        self.cand_table.clear(columns=True)
        self.cand_table.add_columns("Idx", "Src", "Method", "Score", "Year", "DOI", "Title")
        # Candidates should feel like a selectable list (row-based), not a spreadsheet (cell-based)
        self.cand_table.cursor_type = "row"
        self.cand_table.show_cursor = True

    def _selected_row(self) -> EntryRow:
        idx = self.entry_list.index or 0
        return self.state.rows[idx]

    def _current_candidates(self) -> List[Dict]:
        row = self._selected_row()
        if row.entry_id in self._candidate_cache:
            return self._candidate_cache[row.entry_id]
        cands = self.validator.get_ranked_candidates(row.current, limit=15)
        self._candidate_cache[row.entry_id] = cands
        return cands

    def _fmt_fields(self, entry: Dict, keys: List[str]) -> str:
        """Format BibTeX-like entry fields for display."""
        def _one(k: str) -> str:
            v = entry.get(k, "")
            if v is None:
                v = ""
            v = str(v).strip().replace("\n", " ")
            if len(v) > 140:
                v = v[:137] + "..."
            return f"{k}: {v}"

        return "\n".join(_one(k) for k in keys if entry.get(k))

    def _candidate_display_fields(self, corrected_fields: Dict) -> Dict:
        """Extract candidate fields for display."""
        return corrected_fields or {}

    def _canonicalize_val(self, v: object) -> str:
        if v is None:
            return ""
        s = str(v).strip().replace("\n", " ")
        # collapse repeated whitespace for stable diffs
        s = " ".join(s.split())
        return s

    def _diff_blocks(self, *, local_entry: Dict, remote_fields: Dict, keys: List[str]) -> str:
        """
        Build a Rich-markup diff between local_entry and remote_fields.
        Color rules:
          - add (missing locally, present remotely): green background
          - remove (present locally, missing remotely): red background
          - change (both present, different): yellow text (bold, no background)
          - unchanged: normal text (optional; we will show unchanged too, but uncolored)
        """
        lines: List[str] = []
        for k in keys:
            l_has = k in local_entry and self._canonicalize_val(local_entry.get(k)) != ""
            r_has = k in remote_fields and self._canonicalize_val(remote_fields.get(k)) != ""

            l_val = self._canonicalize_val(local_entry.get(k))
            r_val = self._canonicalize_val(remote_fields.get(k))

            if l_has and not r_has:
                # removed by applying remote (remote missing it)
                lines.append(f"[bold on red]{escape(k)}: {escape(l_val)}[/bold on red]")
                continue

            if not l_has and r_has:
                # will be added from remote
                lines.append(f"[bold on green]{escape(k)}: {escape(r_val)}[/bold on green]")
                continue

            if l_has and r_has and l_val != r_val:
                # changed
                # Use yellow text (no background) for readability
                lines.append(
                    f"[bold yellow]{escape(k)}: {escape(l_val)}  →  {escape(r_val)}[/bold yellow]"
                )
                continue

            if l_has and r_has:
                # unchanged but present
                lines.append(f"{escape(k)}: {escape(l_val)}")

        # Also show ID for context (not diff-colored)
        entry_id = self._canonicalize_val(local_entry.get("ID"))
        if entry_id:
            lines.insert(0, f"ID: {escape(entry_id)}")
        return "\n".join(lines) if lines else "(no comparable fields)"

    def _update_details_from_candidate_cursor(self) -> None:
        row = self._selected_row()
        cands = self._current_candidates()
        if not cands:
            self.details.update(self._details_text(row, cands, None))
            return

        idx = self.cand_table.cursor_row
        if idx is None:
            idx = 0
        # Clamp just in case
        idx = max(0, min(idx, len(cands) - 1))

        self._candidate_cursor_by_entry[row.entry_id] = idx
        self.details.update(self._details_text(row, cands, idx))

    def _refresh_right(self) -> None:
        row = self._selected_row()
        cands = self._current_candidates()

        self.cand_table.clear()
        for i, c in enumerate(cands):
            fields = c.get("corrected_fields") or {}
            title = (fields.get("title") or "").replace("\n", " ").strip()
            year = (fields.get("year") or "").strip()
            doi = (fields.get("doi") or "").strip()
            score = float(c.get("score") or 0.0)

            src = c.get("source") or "?"
            if not c.get("accepted_by_gate", False):
                src = f"{src}!"

            self.cand_table.add_row(
                str(i),
                src,
                c.get("search_method") or "",
                f"{score:.2f}",
                year,
                doi[:24] if doi else "",
                title[:70],
            )

        if not cands:
            self.details.update(self._details_text(row, cands, None))
            return

        # Restore last cursor row for this entry (default to 0)
        remembered = self._candidate_cursor_by_entry.get(row.entry_id, 0)
        remembered = max(0, min(remembered, len(cands) - 1))
        self.cand_table.cursor_coordinate = (remembered, 0)
        self._update_details_from_candidate_cursor()

    def _details_text(self, row: EntryRow, cands: List[Dict], selected_idx: Optional[int]) -> str:
        lint_lines = ""
        if row.lint_issues:
            lint_preview = row.lint_issues[:10]
            lint_lines = "Lint:\n  - " + "\n  - ".join(lint_preview)
            if len(row.lint_issues) > 10:
                lint_lines += f"\n  ... and {len(row.lint_issues) - 10} more"
            lint_lines += "\n"

        base = (
            f"Local: {row.entry_id}\n"
            f"Status: {row.status}    Applied: {row.applied_from or ''}\n"
            f"{lint_lines}\n"
        )

        if selected_idx is None or selected_idx >= len(cands):
            # Show local only when no candidate selection exists
            local_only = self._fmt_fields(
                row.current,
                ["ENTRYTYPE", "ID", "title", "author", "year", "doi", "journal", "booktitle", "url"],
            )
            return base + "=== LOCAL (current) ===\n" + (local_only or "(no fields)") + "\n\nNo candidates."

        c = cands[selected_idx]
        fields = c.get("corrected_fields") or {}
        reasons = c.get("gate_reasons") or []
        accepted = bool(c.get("accepted_by_gate"))
        score = float(c.get("score") or 0.0)

        # Diff view (colored)
        diff_keys = ["ENTRYTYPE", "title", "author", "year", "doi", "journal", "booktitle", "url"]
        diff_view = self._diff_blocks(local_entry=row.current, remote_fields=fields, keys=diff_keys)

        right = (
            "=== SELECTED REMOTE ===\n"
            f"Source: {c.get('source')}  Method: {c.get('search_method')}\n"
            f"Score: {score:.2f}  AcceptedByGate: {accepted}\n"
            "\n=== DIFF (local → remote) ===\n"
            f"{diff_view}\n"
        )

        if reasons:
            right += "Gate reasons:\n  - " + "\n  - ".join(reasons[:6])
            if len(reasons) > 6:
                right += f"\n  ... and {len(reasons)-6} more"
        right += "\nENTER: apply this candidate | TAB: switch pane | w: write & quit\n"
        return base + right

    def on_list_view_selected(self, _event: ListView.Selected) -> None:
        self.state.selected_idx = self.entry_list.index or 0
        self._refresh_right()

    def on_list_view_highlighted(self, _event: ListView.Highlighted) -> None:
        # Fired when moving the cursor/highlight with arrow keys.
        # Keep right pane in sync with the currently highlighted entry.
        self.state.selected_idx = self.entry_list.index or 0
        self._refresh_right()

    def on_data_table_cursor_moved(self, _event) -> None:
        self._update_details_from_candidate_cursor()

    def on_data_table_cell_highlighted(self, _event: DataTable.CellHighlighted) -> None:
        # Many Textual versions emit CellHighlighted on keyboard navigation
        self._update_details_from_candidate_cursor()

    def on_data_table_row_highlighted(self, _event) -> None:
        # Some versions emit RowHighlighted; keep preview in sync
        self._update_details_from_candidate_cursor()

    def on_key(self, event: events.Key) -> None:
        # Fallback: ensure preview updates when navigating in the candidates table,
        # even if DataTable doesn't emit cursor_moved/cell_highlighted in this version.
        if self.focused is self.cand_table and event.key in {
            "up",
            "down",
            "left",
            "right",
            "pageup",
            "pagedown",
            "home",
            "end",
        }:
            # Let the DataTable handle the key first, then update preview.
            self.call_after_refresh(self._update_details_from_candidate_cursor)

    def on_data_table_row_selected(self, _event: DataTable.RowSelected) -> None:
        # Enter on the candidate table should apply the selected candidate
        self._update_details_from_candidate_cursor()
        self.action_apply_candidate()

    def action_apply_candidate(self) -> None:
        row = self._selected_row()
        cands = self._current_candidates()
        if not cands:
            row.status = "no_match"
            self._populate_entries()
            self._refresh_right()
            return

        idx = self.cand_table.cursor_row
        if idx is None or idx < 0 or idx >= len(cands):
            return

        cand = cands[idx]
        corrected = cand.get("corrected_fields") or {}
        score = float(cand.get("score") or 0.0)
        is_doi = bool((cand.get("search_method") or "").endswith(":DOI"))
        trusted = is_doi or (score >= TITLE_SIMILARITY_THRESHOLD)

        row.current = apply_candidate_to_entry(
            original=row.original,
            current=row.current,
            corrected_fields=corrected,
            search_method=cand.get("search_method", ""),
            trusted=trusted,
        )
        row.status = "edited"
        row.applied_from = cand.get("search_method")
        self.state.unsaved_changes = True
        self._candidate_cursor_by_entry[row.entry_id] = idx
        self._populate_entries()
        self._refresh_right()

    def action_revert_entry(self) -> None:
        row = self._selected_row()
        row.current = dict(row.original)
        row.status = "unreviewed"
        row.applied_from = None
        self.state.unsaved_changes = True
        self._populate_entries()
        self._refresh_right()

    def action_skip_entry(self) -> None:
        row = self._selected_row()
        row.status = "skipped"
        self._populate_entries()
        self._refresh_right()

    def action_no_match(self) -> None:
        row = self._selected_row()
        row.status = "no_match"
        self._populate_entries()
        self._refresh_right()

    def action_write_and_quit(self) -> None:
        self.edited_entries = [r.current for r in self.state.rows]
        self.exit()


def run_tui_editor(
    *,
    entries: List[Dict],
    validator: SmartBibtexValidator,
    output_path: str,
) -> List[Dict]:
    """
    Run interactive TUI editor for entry review and correction.

    Args:
        entries: List of BibTeX entries to review
        validator: SmartBibtexValidator with validation results
        output_path: Path to write corrected bibliography

    Returns:
        List of edited entries (modified in place by user actions)
    """
    lint_map, _lint_total = lint_entries(entries)
    rows: List[EntryRow] = []
    for e in entries:
        entry_id = e.get("ID", "unknown")
        title = (e.get("title") or "").strip()
        rows.append(
            EntryRow(
                entry_id=entry_id,
                title=title,
                original=dict(e),
                current=dict(e),
                lint_issues=lint_map.get(entry_id, []),
            )
        )

    state = UIState(rows=rows)
    app = BibEditorTextualApp(state=state, validator=validator)
    app.run()

    # If user quit without write&quit, return original entries unchanged
    return app.edited_entries if app.edited_entries is not None else entries
