import html as _html
import re

# Tagi, których treść jest bezwartościowa dla analizy - usuwamy razem z zawartością
_DROP_TAGS = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
# Tagi blokowe -> nowa linia, żeby nie skleić punktów listy w jedno słowo
_BLOCK_TAGS = re.compile(r"</?(p|br|div|li|ul|ol|tr|h[1-6]|section|article)\b[^>]*>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")
_LOOKS_LIKE_HTML = re.compile(r"<(p|br|div|li|ul|ol|span|strong|em|h[1-6]|table)\b", re.IGNORECASE)


def strip_html(text: str) -> str:
    """
    Zamienia HTML na czysty tekst. Część źródeł (JustJoinIT, NoFluffJobs) zwraca
    opisy jako surowy HTML - tagi i encje to czyste marnowanie tokenów w promptcie
    i szum dla modelu.
    """
    if not text:
        return ""

    if not _LOOKS_LIKE_HTML.search(text) and "&" not in text:
        return text

    text = _DROP_TAGS.sub(" ", text)
    text = _BLOCK_TAGS.sub("\n", text)
    text = _ANY_TAG.sub(" ", text)
    text = _html.unescape(text)

    # &nbsp; -> zwykła spacja, potem normalizacja białych znaków
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = re.sub(r"^[ \t]+|[ \t]+$", "", text, flags=re.MULTILINE)

    return text.strip()


def detect_work_mode(location: str = "", description: str = "", title: str = "") -> dict:
    """
    Rozpoznaje tryb pracy (zdalnie, hybryda, stacjonarnie) z metadanych oferty.

    Zwraca słownik: {'label': str, 'icon': str, 'badge_class': str}
    """
    text = f"{location} {title} {description}".lower()
    
    # Wzorce: hybryda
    if any(k in text for k in ['hybryd', 'hybrid', 'częściowo zdaln', 'partly remote', '2-3 dn', '3-2 dn']):
        return {
            'label': 'Hybrydowo',
            'icon': '🏢/🏠',
            'badge_class': 'badge-match-med'
        }
    # Wzorce: pełny zdalny
    elif any(k in text for k in ['zdaln', 'remote', 'home office', '100% zdaln', 'fully remote', 'praca z domu', 'anywhere']):
        return {
            'label': '100% Zdalnie',
            'icon': '🏠',
            'badge_class': 'badge-match-high'
        }
    # Wzorce: stacjonarnie
    else:
        return {
            'label': 'Stacjonarnie',
            'icon': '🏢',
            'badge_class': 'badge-neutral'
        }

def clean_job_description(text: str) -> str:
    """
    Skraca opis oferty o mniej więcej 40-60%, wycinając sekcje nietechniczne.

    Wycina: benefity, RODO i klauzule prawne, opis procesu rekrutacji, marketing.
    Zostawia: wymagania, zakres obowiązków, stack technologiczny.
    """
    if not text:
        return ""

    text = strip_html(text)

    rodo_markers = [
        r"Administratorem danych",
        r"RODO",
        r"Klauzula informacyjna",
        r"Wyrażam zgodę",
        r"Please note that",
        r"Your personal data",
        r"Information clause",
        r"Zgodnie z art\. 13",
        r"Prosimy o dopisanie następującej klauzuli"
    ]
    
    benefits_markers = [
        r"Oferujemy:?",
        r"Co zyskasz:?",
        r"Co oferujemy:?",
        r"Benefity:?",
        r"What we offer:?",
        r"To oferujemy:?",
        r"Benefits:?"
    ]
    
    recruitment_markers = [
        r"Jak aplikować:?",
        r"Etapy rekrutacji:?",
        r"Recruitment process:?",
        r"Proces rekrutacyjny:?"
    ]
    
    all_cutoff_markers = benefits_markers + rodo_markers + recruitment_markers
    pattern = r"(?:^|\n)\s*(" + "|".join(all_cutoff_markers) + r").*($|\n)"
    
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        text = text[:match.start()].strip()
        
    return text.strip()
