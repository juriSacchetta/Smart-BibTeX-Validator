"""Google Scholar validation source"""

from typing import Dict, Optional, Tuple
from .base import ValidationSource

try:
    from scholarly import scholarly
except ImportError:
    scholarly = None


class ScholarSource(ValidationSource):
    """Google Scholar validation source"""
    
    name = "scholar"

    def should_attempt(self, entry: Dict) -> Tuple[bool, str]:
        """Scholar-specific skip policy"""
        if not entry.get("title"):
            return False, "missing title"
        return True, "ok"

    def search_by_doi(self, doi: str) -> Optional[Dict]:
        """Scholar doesn't support DOI search reliably"""
        return None

    def search_by_title(self, title: str) -> Optional[Dict]:
        """Search Scholar by title"""
        if scholarly is None:
            return None
        
        try:
            search_query = scholarly.search_pubs(title)
            result = next(search_query, None)
            return result
        except Exception:
            return None

    def extract_bibtex_fields(self, result: Dict) -> Dict:
        """Extract BibTeX fields from Scholar result"""
        fields = {}

        if "bib" not in result:
            return fields

        bib = result["bib"]

        if "title" in bib:
            fields["title"] = bib["title"]

        if "author" in bib:
            # Scholar returns authors as a string with 'and' separator
            fields["author"] = bib["author"]

        if "pub_year" in bib:
            fields["year"] = str(bib["pub_year"])

        if "venue" in bib:
            fields["venue"] = bib["venue"]

        return fields
