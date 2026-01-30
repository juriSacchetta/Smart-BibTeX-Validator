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

CROSSREF_TYPE_TO_BIBTEX = {
    "proceedings-article": "inproceedings",
    "proceedings": "proceedings",
    "journal-article": "article",
    "book-chapter": "incollection",
    "book": "book",
    "report": "techreport",
    "dissertation": "phdthesis",
    "posted-content": "misc",
}


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

        # Infer BibTeX entry type from Crossref "type"
        cr_type = (result.get("type") or "").strip().lower()
        bibtex_type = CROSSREF_TYPE_TO_BIBTEX.get(cr_type)
        if bibtex_type:
            fields["ENTRYTYPE"] = bibtex_type

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
                parts = date_parts[0]
                if len(parts) >= 1:
                    fields["year"] = str(parts[0])
                if len(parts) >= 2:
                    month_map = {
                        1: "jan", 2: "feb", 3: "mar", 4: "apr", 5: "may", 6: "jun",
                        7: "jul", 8: "aug", 9: "sep", 10: "oct", 11: "nov", 12: "dec",
                    }
                    m = month_map.get(int(parts[1]))
                    if m:
                        fields["month"] = m

        # URL: prefer DOI resolver URL when DOI exists
        if result.get("DOI"):
            fields["doi"] = result["DOI"]
            fields["url"] = f"https://doi.org/{result['DOI']}"
        elif result.get("URL"):
            fields["url"] = result["URL"]

        # Publisher
        if result.get("publisher"):
            fields["publisher"] = result["publisher"]

        # Pages
        if result.get("page"):
            fields["pages"] = str(result["page"])

        # Volume
        if result.get("volume"):
            fields["volume"] = str(result["volume"])

        # Issue/Number
        if result.get("issue"):
            fields["number"] = str(result["issue"])

        # Map container-title appropriately based on entry type
        container_title = None
        ct = result.get("container-title")
        if isinstance(ct, list) and ct:
            container_title = ct[0]
        elif isinstance(ct, str):
            container_title = ct

        if container_title:
            # Proceedings items should populate booktitle; journal items should populate journal
            if bibtex_type in ("inproceedings", "incollection", "proceedings"):
                fields["booktitle"] = container_title
            elif bibtex_type == "article":
                fields["journal"] = container_title
            else:
                # Fallback for unknown types
                fields["booktitle"] = container_title

        return fields

    def _rate_limit(self):
        """Enforce polite rate limiting (Crossref requests ~0.1s apart)"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()
