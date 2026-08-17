"""
Utility Functions Package
Parsowanie CV, modele danych, normalizacja linków i bezpieczny zapis.

Uwaga: GeminiClient został zastąpiony przez waterfall_analysis.py
(kaskada modeli + rotacja kluczy) i przeniesiony do _archive/.
"""

from .cv_parser import CVParser
from .data_models import Job, JobMatch
from .link_fetcher import extract_job_info_from_url
from .links import canonical_link
from .safe_io import load_json_safe, save_json_atomic

__all__ = [
    'CVParser',
    'Job',
    'JobMatch',
    'extract_job_info_from_url',
    'canonical_link',
    'load_json_safe',
    'save_json_atomic',
]
