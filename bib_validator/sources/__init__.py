"""Validation sources for BibTeX entries"""

from typing import Dict, List
from .base import ValidationSource
from .dblp import DBLPSource
from .scholar import ScholarSource
from .semantic import SemanticScholarSource

DEFAULT_ORDER = ["dblp", "scholar", "semantic"]


def build_sources(selected: List[str]) -> Dict[str, ValidationSource]:
    """Build sources in order, filtering by selection"""
    sources: Dict[str, ValidationSource] = {}
    
    for source_name in DEFAULT_ORDER:
        if source_name not in selected:
            continue
        
        if source_name == "dblp":
            sources["dblp"] = DBLPSource()
        elif source_name == "scholar":
            sources["scholar"] = ScholarSource()
        elif source_name == "semantic":
            sources["semantic"] = SemanticScholarSource()
    
    return sources
