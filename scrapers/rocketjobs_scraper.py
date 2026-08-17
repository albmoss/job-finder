"""
RocketJobs.pl Scraper (candidate-api)

Poprzednia wersja sniffowała ruch sieciowy przez Playwright pod adresem
/oferty-pracy/<miasto>/<poziomy> i konsekwentnie zwracała 0 ofert - taki URL
nie istnieje, a filtry z configu ("staz", "asystent") nie są prawidłowymi
wartościami experienceLevels.

RocketJobs wystawia to samo candidate-api co JustJoin.it, z pełnym opisem
oferty w polu `body`, więc cała logika siedzi w CandidateAPIScraper.
"""

from scrapers.candidate_api_base import CandidateAPIScraper


class RocketJobsScraper(CandidateAPIScraper):
    """Scraper RocketJobs.pl - HTTP API, bez przeglądarki."""

    PORTAL_URL = "https://rocketjobs.pl"
    SOURCE_NAME = "RocketJobs"
    CONFIG_KEY = "rocketjobs"

    def build_link(self, slug: str) -> str:
        return f"{self.PORTAL_URL}/oferta/{slug}"
