"""Semantic Scholar validation source"""

from typing import Dict, Optional, Tuple
from .base import ValidationSource

try:
    from semanticscholar import SemanticScholar
except ImportError:
    SemanticScholar = None


class SemanticScholarSource(ValidationSource):
    """Semantic Scholar validation source"""
    
    name = "semantic"

    def __init__(self):
        if SemanticScholar is None:
            self.sch = None
        else:
            self.sch = SemanticScholar()

    def should_attempt(self, entry: Dict) -> Tuple[bool, str]:
        """Semantic Scholar-specific skip policy"""
        if not entry.get("doi") and not entry.get("title"):
            return False, "missing doi and title"
        return True, "ok"

    def search_by_doi(self, doi: str) -> Optional[Dict]:
        """Search Semantic Scholar by DOI"""
        if self.sch is None:
            return None
        
        try:
            paper = self.sch.get_paper(f"DOI:{doi}")
            return paper
        except Exception:
            return None

    def search_by_title(self, title: str) -> Optional[Dict]:
        """Search Semantic Scholar by title"""
        if self.sch is None:
            return None
        
        try:
            results = self.sch.search_paper(title, limit=1)
            if results and len(results) > 0:
                return results[0]
            return None
        except Exception:
            return None

    def extract_bibtex_fields(self, result: Dict) -> Dict:
        """Extract BibTeX fields from Semantic Scholar result"""
        fields = {}

        if hasattr(result, "title") and result.title:
            fields["title"] = result.title

        if hasattr(result, "authors") and result.authors:
            authors = [author.name for author in result.authors if hasattr(author, "name")]
            if authors:
                fields["author"] = " and ".join(authors)

        if hasattr(result, "year") and result.year:
            fields["year"] = str(result.year)

        if hasattr(result, "venue") and result.venue:
            fields["venue"] = result.venue

        if hasattr(result, "externalIds") and result.externalIds:
            if "DOI" in result.externalIds:
                fields["doi"] = result.externalIds["DOI"]

        return fields
