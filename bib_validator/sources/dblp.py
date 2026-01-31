"""DBLP validation source"""

import logging
import requests
import time
from typing import Dict, Optional, Tuple, List
from .base import ValidationSource
from ..lint import is_web_resource


logger = logging.getLogger(__name__)


class DBLPSource(ValidationSource):
    """DBLP validation source"""
    
    name = "dblp"

    def __init__(self):
        """Initialize with session for connection reuse"""
        self.session = requests.Session()

    def should_attempt(self, entry: Dict) -> Tuple[bool, str]:
        """DBLP-specific skip policy"""
        entry_type = entry.get("ENTRYTYPE", "").lower()
        title = (entry.get("title") or "").lower()

        # Skip web resources entirely (handled separately by validator)
        if is_web_resource(entry):
            return False, "web resource (handled separately)"

        # Skip online/misc/manual without DOI
        if entry_type in ("online", "misc", "manual") and not entry.get("doi"):
            return False, f"{entry_type} without DOI"

        # Skip based on title patterns (GitHub, blogs, docs, etc.)
        skip_patterns = [
            "github.com",
            "github issue",
            "pull request",
            "documentation",
            "readme",
            "security policy",
            "vulnerability disclosure",
            "nasa.gov",
            "esa.int",
            "manual",
            "guide",
            "tutorial",
            "blog",
            "website",
            "webpage",
        ]

        for pattern in skip_patterns:
            if pattern in title:
                return False, f"title contains '{pattern}'"

        return True, "ok"

    def search_by_doi(self, doi: str) -> Optional[Dict]:
        """Search DBLP by DOI"""
        params = {"q": f"doi:{doi}", "format": "json", "h": 1}
        return self._search(params)

    def search_by_title(self, title: str) -> Optional[Dict]:
        """Search DBLP by title"""
        params = {"q": title, "format": "json", "h": 1}
        return self._search(params)

    def search_by_doi_candidates(self, doi: str, max_results: int = 5) -> List[Dict]:
        """Search DBLP by DOI and return up to max_results candidates"""
        params = {"q": f"doi:{doi}", "format": "json", "h": max_results}
        return self._search_many(params, max_results=max_results)

    def search_by_title_candidates(self, title: str, max_results: int = 5) -> List[Dict]:
        """Search DBLP by title and return up to max_results candidates"""
        params = {"q": title, "format": "json", "h": max_results}
        return self._search_many(params, max_results=max_results)

    def _search(self, params: Dict, max_retries: int = 2) -> Optional[Dict]:
        """Search DBLP with exponential backoff retry"""
        url = "https://dblp.org/search/publ/api"
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=10)

                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.debug("DBLP rate limited (429). Waiting %.1fs params=%s", wait_time, params)
                        time.sleep(wait_time)
                        continue
                    return None

                response.raise_for_status()
                data = response.json()

                if (
                    "result" in data
                    and "hits" in data["result"]
                    and "hit" in data["result"]["hits"]
                ):
                    hits = data["result"]["hits"]["hit"]
                    if hits:
                        return hits[0]["info"]

                logger.debug("DBLP no hits params=%s", params)
                return None

            except requests.exceptions.RequestException:
                logger.debug("DBLP request failed attempt=%d/%d params=%s", attempt + 1, max_retries, params, exc_info=True)
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                return None

        return None

    def _search_many(self, params: Dict, max_results: int = 5, max_retries: int = 2) -> List[Dict]:
        """Search DBLP and return a list of hit infos (up to max_results)."""
        url = "https://dblp.org/search/publ/api"
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=10)

                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.debug("DBLP rate limited (429). Waiting %.1fs params=%s", wait_time, params)
                        time.sleep(wait_time)
                        continue
                    return []

                response.raise_for_status()
                data = response.json()

                hits = (
                    data.get("result", {})
                    .get("hits", {})
                    .get("hit", [])
                )
                if not hits:
                    logger.debug("DBLP no hits params=%s", params)
                    return []

                out: List[Dict] = []
                for h in hits[:max_results]:
                    info = (h or {}).get("info")
                    if info:
                        out.append(info)
                return out

            except requests.exceptions.RequestException:
                logger.debug("DBLP request failed attempt=%d/%d params=%s", attempt + 1, max_retries, params, exc_info=True)
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                return []

        return []

    def extract_bibtex_fields(self, result: Dict) -> Dict:
        """Extract BibTeX fields from DBLP result"""
        fields = {}

        if "title" in result:
            fields["title"] = result["title"]

        if "authors" in result and "author" in result["authors"]:
            author_data = result["authors"]["author"]
            if isinstance(author_data, list):
                authors = [a["text"] if isinstance(a, dict) else a for a in author_data]
            else:
                authors = [author_data["text"] if isinstance(author_data, dict) else author_data]
            fields["author"] = " and ".join(authors)

        if "year" in result:
            fields["year"] = str(result["year"])

        if "venue" in result:
            fields["venue"] = result["venue"]

        if "doi" in result:
            fields["doi"] = result["doi"]

        return fields
