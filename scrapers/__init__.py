"""
Scrapery polskich portali z ogłoszeniami o pracę.
"""

from .base_scraper import BaseScraper
from .pracuj_optimized_scraper import PracujOptimizedScraper
from .olx_scraper import OLXScraper
from .rocketjobs_scraper import RocketJobsScraper
from .linkedin_scraper import LinkedInScraper
from .nofluff_scraper import NoFluffScraper
from .justjoin_scraper import JustJoinScraper

# Scrapery oparte o API
from .solid_jobs_api import SolidJobsAPIScraper
from .adzuna_api import AdzunaAPIScraper
from .jooble_api import JoobleAPIScraper
from .careerjet_api import CareerjetAPIScraper

# Portale ogólne (nie-IT) na wspólnej bazie ld+json
from .ldjson_scraper_base import LdJsonPortalScraper
from .praca_pl_scraper import PracaPlScraper
from .aplikuj_scraper import AplikujScraper
from .gowork_scraper import GoWorkScraper

# Playwright - Indeed wymaga przeglądarki (Cloudflare)
from .indeed_scraper import IndeedScraper

__all__ = [
    'BaseScraper',
    'PracujOptimizedScraper',
    'OLXScraper',
    'RocketJobsScraper',
    'LinkedInScraper',
    'NoFluffScraper',
    'JustJoinScraper',
    # scrapery API
    'SolidJobsAPIScraper',
    'AdzunaAPIScraper',
    'JoobleAPIScraper',
    'CareerjetAPIScraper',
    # Portale ogólne (ld+json)
    'LdJsonPortalScraper',
    'PracaPlScraper',
    'AplikujScraper',
    'GoWorkScraper',
    # scrapery przeglądarkowe
    'IndeedScraper',
]
