"""Crossref validation source"""

import logging
import requests
import time
from typing import Dict, Optional, Tuple
from .base import ValidationSource


logger = logging.getLogger(__name__)

CROSSREF_API_URL = "https://api.crossref.org/works"
CROSSREF_REQUEST_TIMEOUT = 8.0
CROSSREF_USER_AGENT = "bib-validator/1.0 (https://github.com/you/bib-validator; mailto:maintainer@example.com)"


class CrossrefSource(ValidationSource):
    """Crossref validation source using REST API"""
    
    name = "crossref"

    def __init__(self):
        """Initialize with rate-limit tracking and session for connection reuse"""
        self.last_request_time = 0.0
        self.min_request_interval = 0.1  # Polite rate limit
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CROSSREF_USER_AGENT})

    def should_attempt(self, entry: Dict) -> Tuple[bool, str]:
        """Crossref-specific skip policy"""
        if not entry.get("doi") and not entry.get("title"):
            return False, "missing doi and title"
        return True, "ok"

    def search_by_doi(self, doi: str) -> Optional[Dict]:
        """Search Crossref by DOI"""
        self._rate_limit()
        
        url = f"{CROSSREF_API_URL}/{doi}"
        
        try:
            response = self.session.get(url, timeout=CROSSREF_REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") == "ok" and data.get("message"):
                return data["message"]
            return None
        except requests.exceptions.Timeout:
            logger.debug("Crossref DOI query timeout doi=%s", doi)
            return None
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug("Crossref DOI not found doi=%s", doi)
            else:
                logger.debug("Crossref DOI query failed doi=%s status=%d", doi, e.response.status_code)
            return None
        except Exception as e:
            logger.debug("Crossref DOI search failed doi=%s", doi, exc_info=True)
            return None

    def search_by_title(self, title: str) -> Optional[Dict]:
        """Search Crossref by title"""
        self._rate_limit()
        
        params = {
            "query.title": title,
            "rows": 1,
            "sort": "relevance",
        }
        
        try:
            response = self.session.get(CROSSREF_API_URL, params=params, timeout=CROSSREF_REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") == "ok" and data.get("message"):
                items = data["message"].get("items", [])
                if items:
                    return items[0]
            return None
        except requests.exceptions.Timeout:
            logger.debug("Crossref title query timeout title=%r", title)
            return None
        except requests.exceptions.HTTPError as e:
            logger.debug("Crossref title query failed title=%r status=%d", title, e.response.status_code)
            return None
        except Exception as e:
            logger.debug("Crossref title search failed title=%r", title, exc_info=True)
            return None

    def extract_bibtex_fields(self, result: Dict) -> Dict:
        """Extract BibTeX fields from Crossref API result"""
        fields = {}

        if result.get("title"):
            titles = result["title"]
            if isinstance(titles, list) and titles:
                fields["title"] = titles[0]
            elif isinstance(titles, str):
                fields["title"] = titles

        if result.get("author"):
            authors = []
            for author in result["author"]:
                name_parts = []
                if author.get("given"):
                    name_parts.append(author["given"])
                if author.get("family"):
                    name_parts.append(author["family"])
                if name_parts:
                    authors.append(" ".join(name_parts))
            if authors:
                fields["author"] = " and ".join(authors)

        if result.get("issued"):
            date_parts = result["issued"].get("date-parts")
            if date_parts and date_parts[0]:
                fields["year"] = str(date_parts[0][0])

        # Map container-title to venue (booktitle/journal context-dependent)
        if result.get("container-title"):
            container = result["container-title"]
            if isinstance(container, list) and container:
                fields["venue"] = container[0]
            elif isinstance(container, str):
                fields["venue"] = container

        if result.get("DOI"):
            fields["doi"] = result["DOI"]

        return fields

    def _rate_limit(self):
        """Enforce polite rate limiting (Crossref requests ~0.1s apart)"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()
