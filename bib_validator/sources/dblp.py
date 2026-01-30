"""DBLP validation source"""

import requests
import time
from typing import Dict, Optional, Tuple
from .base import ValidationSource


class DBLPSource(ValidationSource):
    """DBLP validation source"""
    
    name = "dblp"

    def should_attempt(self, entry: Dict) -> Tuple[bool, str]:
        """DBLP-specific skip policy"""
        entry_type = entry.get("ENTRYTYPE", "").lower()
        title = (entry.get("title") or "").lower()

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

        if entry_type == "online" and not entry.get("doi"):
            return False, "online entry without DOI"

        if entry_type in ["techreport", "misc", "manual"] and not entry.get("doi"):
            return False, f"{entry_type} without DOI"

        return True, "ok"

    def search_by_doi(self, doi: str) -> Optional[Dict]:
        """Search DBLP by DOI"""
        params = {"q": f"doi:{doi}", "format": "json", "h": 1}
        return self._search(params)

    def search_by_title(self, title: str) -> Optional[Dict]:
        """Search DBLP by title"""
        params = {"q": title, "format": "json", "h": 1}
        return self._search(params)

    def _search(self, params: Dict, max_retries: int = 3) -> Optional[Dict]:
        """Search DBLP with exponential backoff retry"""
        url = "https://dblp.org/search/publ/api"
        retry_delay = 3

        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=15)

                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        print(f"  ⏳ Rate limited. Waiting {wait_time}s...")
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

                return None

            except requests.exceptions.RequestException:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                return None

        return None

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
