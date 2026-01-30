"""Validation sources for BibTeX entries"""

from typing import Dict, List
from .base import ValidationSource
from .crossref import CrossrefSource
from .dblp import DBLPSource
from .semantic import SemanticScholarSource

DEFAULT_ORDER = ["crossref", "dblp", "semantic"]


def build_sources(selected: List[str]) -> Dict[str, ValidationSource]:
    """Build sources in order, filtering by selection"""
    sources: Dict[str, ValidationSource] = {}
    
    for source_name in DEFAULT_ORDER:
        if source_name not in selected:
            continue
        
        if source_name == "crossref":
            sources["crossref"] = CrossrefSource()
        elif source_name == "dblp":
            sources["dblp"] = DBLPSource()
        elif source_name == "semantic":
            sources["semantic"] = SemanticScholarSource()
    
    return sources
