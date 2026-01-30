"""Semantic Scholar validation source"""

import logging
import time
import requests
from typing import Dict, Optional, Tuple
from .base import ValidationSource


logger = logging.getLogger(__name__)

# Semantic Scholar REST API endpoint
S2_API_URL = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,authors,year,venue,externalIds"
S2_REQUEST_TIMEOUT = 8.0  # Hard timeout for HTTP requests


class SemanticScholarSource(ValidationSource):
    """Semantic Scholar validation source"""
    
    name = "semantic"

    def __init__(self):
        """Initialize with cooldown tracking and session for connection reuse"""
        self.cooldown_until = 0.0
        self.session = requests.Session()

    def should_attempt(self, entry: Dict) -> Tuple[bool, str]:
        """Semantic Scholar-specific skip policy"""
        if time.time() < self.cooldown_until:
            return False, f"rate limited until {self.cooldown_until:.0f}"
        
        if not entry.get("doi") and not entry.get("title"):
            return False, "missing doi and title"
        return True, "ok"

    def search_by_doi(self, doi: str) -> Optional[Dict]:
        """Search Semantic Scholar by DOI via REST API"""
        url = f"{S2_API_URL}/paper/DOI:{doi}"
        params = {"fields": S2_FIELDS}
        
        try:
            response = self.session.get(url, params=params, timeout=S2_REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            # Check if we got a valid paper result (has paperId)
            if data.get("paperId"):
                return data
            return None
        except requests.exceptions.Timeout:
            logger.debug("Semantic Scholar DOI query timeout doi=%s", doi)
            return None
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                self.cooldown_until = time.time() + 60
                logger.warning("Semantic Scholar rate-limited (429). Cooling down for 60s.")
                return None
            elif e.response.status_code == 404:
                logger.debug("Semantic Scholar DOI not found doi=%s", doi)
            else:
                logger.debug("Semantic Scholar DOI query failed doi=%s status=%d", doi, e.response.status_code, exc_info=True)
            return None
        except Exception as e:
            logger.debug("Semantic Scholar DOI search failed doi=%s", doi, exc_info=True)
            return None

    def search_by_title(self, title: str) -> Optional[Dict]:
        """Search Semantic Scholar by title via REST API"""
        url = f"{S2_API_URL}/paper/search"
        params = {
            "query": title,
            "limit": 1,
            "fields": S2_FIELDS,
        }
        
        try:
            response = self.session.get(url, params=params, timeout=S2_REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            # Check if we got results
            if data.get("data") and len(data["data"]) > 0:
                return data["data"][0]
            return None
        except requests.exceptions.Timeout:
            logger.debug("Semantic Scholar title query timeout title=%r", title)
            return None
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                self.cooldown_until = time.time() + 60
                logger.warning("Semantic Scholar rate-limited (429). Cooling down for 60s.")
                return None
            elif e.response.status_code == 400:
                logger.debug("Semantic Scholar rejected query (400) title=%r", title)
            else:
                logger.debug("Semantic Scholar title query failed title=%r status=%d", title, e.response.status_code, exc_info=True)
            return None
        except Exception as e:
            logger.debug("Semantic Scholar title search failed title=%r", title, exc_info=True)
            return None

    def extract_bibtex_fields(self, result: Dict) -> Dict:
        """Extract BibTeX fields from Semantic Scholar REST API result"""
        fields = {}

        if result.get("title"):
            fields["title"] = result["title"]

        if result.get("authors"):
            authors = [author.get("name", "") for author in result["authors"] if author.get("name")]
            if authors:
                fields["author"] = " and ".join(authors)

        if result.get("year"):
            fields["year"] = str(result["year"])

        if result.get("venue"):
            fields["venue"] = result["venue"]

        if result.get("externalIds"):
            ext_ids = result["externalIds"]
            if ext_ids.get("DOI"):
                fields["doi"] = ext_ids["DOI"]

        return fields
