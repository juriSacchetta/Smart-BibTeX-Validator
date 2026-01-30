"""Base class for validation sources"""

from typing import Dict, Optional, Tuple


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

    def extract_bibtex_fields(self, result: Dict) -> Dict:
        """Convert source-specific result to standard BibTeX fields"""
        raise NotImplementedError
