"""Base class for validation sources"""

from typing import Dict, Optional, Tuple, List


class ValidationSource:
    """Base class for validation sources"""
    
    name: str = "base"

    def should_attempt(self, entry: Dict) -> Tuple[bool, str]:
        """
        Per-source skip logic.
        
        Returns:
            (True, reason) to attempt validation
            (False, reason) to skip this entry for this source
        """
        return True, "default"

    def search_by_doi(self, doi: str) -> Optional[Dict]:
        """Search for entry by DOI"""
        raise NotImplementedError

    def search_by_title(self, title: str) -> Optional[Dict]:
        """Search for entry by title"""
        raise NotImplementedError

    # ---- Multi-candidate APIs (new) ----
    # Default behavior: call the existing single-result APIs.
    # Sources can override these to return richer candidate lists.
    def search_by_doi_candidates(self, doi: str, max_results: int = 5) -> List[Dict]:
        """Search by DOI and return up to max_results candidate results (usually 0/1)."""
        r = self.search_by_doi(doi)
        return [r] if r else []

    def search_by_title_candidates(self, title: str, max_results: int = 5) -> List[Dict]:
        """Search by title and return up to max_results candidate results."""
        r = self.search_by_title(title)
        return [r] if r else []

    def extract_bibtex_fields(self, result: Dict) -> Dict:
        """Convert source-specific result to standard BibTeX fields"""
        raise NotImplementedError
