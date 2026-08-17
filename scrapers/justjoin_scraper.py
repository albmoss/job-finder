"""
JustJoin.it Scraper (candidate-api)

Cała logika pobierania siedzi w CandidateAPIScraper - JustJoin.it i RocketJobs
dzielą identyczne API, więc utrzymujemy jedną implementację zamiast dwóch.

Zmiana względem poprzedniej wersji: opisy dociągane były tylko dla pierwszych
200 ofert i sekwencyjnie (0.5s na ofertę). Teraz limit jest wyższy, a pobieranie
równoległe - stąd w bazie zostały rekordy z opisem "Scraped from JJIT. Tags: ...".
"""

from scrapers.candidate_api_base import CandidateAPIScraper


class JustJoinScraper(CandidateAPIScraper):
    """Scraper JustJoin.it - HTTP API, bez przeglądarki."""

    PORTAL_URL = "https://justjoin.it"
    SOURCE_NAME = "JustJoinIT"
    CONFIG_KEY = "justjoinit"

    def build_link(self, slug: str) -> str:
        return f"{self.PORTAL_URL}/offers/{slug}"
