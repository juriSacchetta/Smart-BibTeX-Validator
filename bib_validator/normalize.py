"""BibTeX field normalization - consistent handling of DOI, title, and author formatting"""

import re
from typing import Optional


def normalize_doi(doi: str) -> Optional[str]:
    """
    Normalize DOI to canonical form: 10.xxxx/...
    
    Strips common prefixes users paste:
      - https://doi.org/
      - http://doi.org/
      - doi: (case-insensitive)
    
    Returns normalized DOI (just '10.xxxx/...') or None if invalid.
    """
    if not doi:
        return None
    
    doi = doi.strip()
    
    # Strip URL prefixes
    doi = re.sub(r"^https?://doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    
    doi = doi.strip()
    
    # Validate: must start with 10. and contain /
    if not re.match(r"^10\.\d{4,9}/\S+$", doi, re.IGNORECASE):
        return None
    
    return doi.lower()


def normalize_title_for_query(title: str) -> str:
    """
    Clean BibTeX/LaTeX artifacts from titles before sending to remote search APIs.
    This is used ONLY for remote queries, NOT for comparison or output.
    
    Removes:
      - LaTeX commands like \\textquoteright{...} -> ...
      - Standalone LaTeX commands like \\alpha, \\&
      - Braces
      - Collapses whitespace
    
    Example:
      "The {\\alpha}-{\\beta} Problem" -> "The alpha beta Problem"
    """
    if not title:
        return ""
    
    # Remove LaTeX commands with arguments like \textquoteright{...} -> keep argument
    title = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", title)
    
    # Remove standalone LaTeX commands like \alpha, \beta, \& etc.
    # Match backslash followed by one or more letters (e.g., \alpha, \textit)
    # Also match special single-char commands like \&, \%, \_
    title = re.sub(r"\\[a-zA-Z]+", " ", title)  # \alpha, \beta, \textit, etc.
    title = re.sub(r"\\[&%_]", " ", title)      # \&, \%, \_, etc.
    
    # Drop braces
    title = title.replace("{", "").replace("}", "")
    
    # Collapse whitespace
    title = " ".join(title.split())
    
    return title


def normalize_author_string(author_str: str) -> str:
    """
    Normalize author string to canonical form.
    
    Ensures authors are separated by " and " (BibTeX standard).
    Handles:
      - "and"-separated: "Smith, J. and Doe, J."
      - comma-separated: "Smith, J., Doe, J."
      - semicolon-separated: "Smith, J.; Doe, J."
    
    Args:
        author_str: raw author string
    
    Returns:
        Normalized string with " and " separators
    """
    if not author_str:
        return ""
    
    # Replace common author separators with " and "
    # Handle "and" (case-insensitive)
    author_str = re.sub(r"\s+and\s+", " and ", author_str, flags=re.IGNORECASE)
    
    # Replace semicolons with " and "
    author_str = author_str.replace(";", " and ")
    
    # Replace commas that separate authors (followed by capital letter or lowercase name)
    # This is a heuristic: comma followed by space and capital letter = new author
    author_str = re.sub(r",\s+(?=[A-Z])", " and ", author_str)
    
    # Split by " and ", strip, and rejoin
    parts = [p.strip() for p in author_str.split(" and ") if p.strip()]
    
    return " and ".join(parts)


def normalize_year(year_str: str) -> Optional[str]:
    """
    Extract and return valid 4-digit year, or None.
    
    Args:
        year_str: raw year string, e.g., "2023", "2023-01-01", etc.
    
    Returns:
        4-digit year string or None if not found
    """
    if not year_str:
        return None
    
    year_str = str(year_str).strip()
    
    # Extract first 4-digit sequence
    match = re.search(r"\d{4}", year_str)
    if match:
        return match.group(0)
    
    return None
