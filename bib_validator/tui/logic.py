"""TUI logic for applying candidates and modifying entries"""

from typing import Dict
from copy import deepcopy


def apply_candidate_to_entry(
    original: Dict,
    current: Dict,
    corrected_fields: Dict,
    search_method: str = "",
    trusted: bool = False,
) -> Dict:
    """
    Apply a candidate correction to an entry.
    
    Args:
        original: Original entry (never modified)
        current: Current working entry (may already have edits)
        corrected_fields: Fields from the remote candidate
        search_method: Source and method (e.g., "crossref:DOI")
        trusted: If True, do full replacement; if False, only fill missing fields
        
    Returns:
        Updated entry dict
    """
    result = deepcopy(current)
    
    # Preserve ID and ENTRYTYPE always
    original_id = result.get("ID")
    original_type = result.get("ENTRYTYPE")
    
    if trusted:
        # Full replacement: keep only ID, add all corrected fields
        result = {"ID": original_id}
        for field, value in corrected_fields.items():
            if not value or field == "ID":
                continue
            result[field] = value
        
        # Ensure ENTRYTYPE exists
        if "ENTRYTYPE" not in result or not result["ENTRYTYPE"]:
            result["ENTRYTYPE"] = original_type or "misc"
    else:
        # Conservative: only fill missing fields
        for field, value in corrected_fields.items():
            if not value or field == "ID":
                continue
            if not result.get(field):
                result[field] = value
    
    # Add provenance
    if search_method:
        result["x-found-in"] = search_method
    
    return result
