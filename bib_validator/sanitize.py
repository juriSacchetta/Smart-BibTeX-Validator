"""BibTeX entry field sanitization for clean output"""

import re
from typing import Dict
from .normalize import normalize_doi, normalize_year
from .url_check import is_doi_url


def latex_encode(s: str) -> str:
    """
    Encode string to LaTeX-safe representation using latexcodec.
    Converts Unicode punctuation and special characters to LaTeX equivalents.
    
    Args:
        s: Input string
    
    Returns:
        LaTeX-encoded string (ASCII-safe for BibTeX)
    """
    if not s:
        return s
    
    try:
        # Use latexcodec to encode Unicode to LaTeX-safe ASCII
        return s.encode("latex").decode("ascii")
    except (UnicodeEncodeError, LookupError):
        # Fallback: if latexcodec unavailable or encoding fails, return original
        # (latexcodec should always be available per requirements.txt)
        return s


def sanitize_entry(entry: Dict) -> Dict:
    """
    Sanitize a BibTeX entry for output:
      - Remove empty/whitespace-only fields
      - Normalize whitespace (collapse consecutive spaces)
      - Encode to LaTeX-safe representation (latexcodec) for text fields
      - Normalize DOI to canonical form (10.xxxx/...)
      - Ensure year is 4 digits if present
      - Strip trailing/leading whitespace from all fields
      - Skip LaTeX encoding for URL, DOI, ID, ENTRYTYPE (preserve raw values)
      - Drop DOI resolver URLs when a DOI field exists (avoid redundancy)
    
    Args:
        entry: BibTeX entry dict
    
    Returns:
        Sanitized copy of entry
    """
    sanitized = {}
    
    # Fields that should NOT be LaTeX-encoded (raw/literal values)
    no_encode_fields = {"url", "doi", "ID", "ENTRYTYPE"}
    
    for key, value in entry.items():
        # Preserve non-string fields (like entry type markers)
        if not isinstance(value, str):
            sanitized[key] = value
            continue
        
        # Strip and collapse whitespace
        value = value.strip()
        value = re.sub(r"\s+", " ", value)
        
        # Skip empty fields
        if not value:
            continue
        
        # Special handling for DOI: normalize to canonical form
        if key.lower() == "doi":
            normalized_doi = normalize_doi(value)
            if normalized_doi:
                value = normalized_doi
        # Special handling for year: ensure 4 digits
        elif key.lower() == "year":
            normalized_year = normalize_year(value)
            if normalized_year:
                value = normalized_year
        # LaTeX-encode text fields, but NOT URLs, DOIs, IDs, or entry types
        elif key.lower() not in no_encode_fields:
            value = latex_encode(value)
        
        sanitized[key] = value
    
    # If we have a DOI, don't keep a redundant DOI resolver URL in `url`.
    # Keep `url` only if it's not a DOI URL (i.e., some landing page / PDF / arXiv / etc.).
    doi_val = (sanitized.get("doi") or "").strip()
    url_val = (sanitized.get("url") or "").strip()
    
    if doi_val and url_val and is_doi_url(url_val):
        sanitized.pop("url", None)
    
    return sanitized


def sanitize_entries(entries: list) -> list:
    """
    Sanitize a list of BibTeX entries.
    
    Args:
        entries: List of entry dicts
    
    Returns:
        List of sanitized entry dicts
    """
    return [sanitize_entry(e) for e in entries]
