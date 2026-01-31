"""TUI state management"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class EntryRow:
    """Represents a single entry in the TUI list"""
    entry_id: str
    title: str
    original: Dict
    current: Dict
    lint_issues: List[str]
    status: str = "unreviewed"  # unreviewed|edited|skipped|no_match
    applied_from: Optional[str] = None  # e.g. "crossref:Title"


@dataclass
class UIState:
    """Global TUI state"""
    rows: List[EntryRow]
    selected_idx: int = 0
    unsaved_changes: bool = False
