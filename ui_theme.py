"""
Paleta i kroje pisma aplikacji - jedno miejsce prawdy.

Tokeny V2 (ciemny motyw) odpowiadają zmiennym `v2-*` z pliku projektowego
`../design/jobfinder.pen` - nazwa tokenu CSS to nazwa zmiennej bez `v2-`.
Backend podaje je w bootstrap API, frontend czyta z `theme_tokens.css`.

    python ui_theme.py            # generuje frontend/src/theme_tokens.css
"""
from pathlib import Path

# Kolejność = kolejność w wygenerowanym pliku. Wartości z pen.dev (motyw dark).
TOKENS = {
    # powierzchnie
    "bg": "#0D0D0E",
    "surface": "rgba(24, 24, 26, 0.8)",
    "surface-solid": "#232326",
    "sheet": "rgba(22, 22, 24, 0.95)",
    "scrim": "rgba(10, 10, 11, 0.65)",
    "shadow": "rgba(0, 0, 0, 0.45)",
    # tusz
    "ink": "#F2F1EE",
    "ink-2": "#A3A29E",
    "ink-3": "#6B6A67",
    # wypełnienia i linie
    "fill-faint": "rgba(255, 255, 255, 0.02)",
    "fill": "rgba(242, 241, 238, 0.04)",
    "fill-2": "rgba(255, 255, 255, 0.05)",
    "fill-3": "rgba(255, 255, 255, 0.08)",
    "fill-active": "rgba(255, 255, 255, 0.09)",
    "stroke": "rgba(255, 255, 255, 0.07)",
    "stroke-strong": "rgba(255, 255, 255, 0.12)",
    "line": "rgba(255, 255, 255, 0.08)",
    "track": "#2A2A2D",
    "level": "rgba(237, 235, 230, 0.25)",
    "rail": "rgba(237, 235, 230, 0.4)",
    # akcent: kobalt w UI, kobalt -> fiolet tylko w strumieniu i odcisku dopasowania
    "accent": "#5A70FF",
    "accent-soft": "rgba(90, 112, 255, 0.65)",
    "accent-faint": "rgba(90, 112, 255, 0.2)",
    "accent-2": "#4B6BFF",
    "stream-end": "#8B5CFF",
    "accent-hot": "#F1ECFF",
    # promienie
    "r-panel": "28px",
    "r-row": "18px",
    "r-well": "11px",
    "r-chip": "10px",
    "r-cell": "7px",
    # pismo
    "font": "'Geist', system-ui, -apple-system, 'Segoe UI', sans-serif",
    "mono": "'Geist Mono', ui-monospace, 'Cascadia Mono', monospace",
}

FONT_URL = "https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600&family=Geist+Mono:wght@400;500&display=swap"


# Barwy stanów decyzji są wspólne dla wszystkich schematów - to one budują
# pamięć wzrokową ("zielone = wysłane") i nie powinny się zmieniać razem
# z akcentem. Każdy akcent musi być od nich odsunięty na kole barw, inaczej
# ikona decyzji zlewa się z metryką dopasowania.
STATES = {
    "moss": "#5EB065",   # wysłane (akcent hue 125deg)
    "slate": "#5B8BA0",  # zapisane
    "amber": "#D9A441",  # aspiruję
    "clay": "#B4705C",   # odrzucone
    "grey": "#98917F",   # ocenione
}


# Barwa portalu. Kulka przy nazwie źródła jest w aplikacji wszędzie ta sama:
# w wierszu oferty, w szczegółach, na karcie i w logu pobrań - dzięki temu
# "skąd to jest" czyta się jednym rzutem oka, bez czytania nazwy.
#
# Dobór barw ma dwa ograniczenia. Po pierwsze: żadnego żółtego. Po drugie:
# odcienie muszą trzymać się z dala od barw decyzji (STATES), bo obie rodziny
# kropek bywają w jednym wierszu - dlatego portale dostają barwy jaśniejsze
# i bardziej nasycone, a kulka portalu jest mniejsza od kropki decyzji.
SOURCES = {
    "pracuj.pl":    "#D9764A",   # pomarańcz
    "aplikuj.pl":   "#5CAE86",   # zieleń
    "olx praca":    "#35AFA6",   # morski
    "praca.pl":     "#6E7FD4",   # indygo
    "gowork.pl":    "#A472CE",   # fiolet
    "rocketjobs":   "#D95C86",   # róż
    "justjoinit":   "#D9564F",   # koral
    "linkedin":     "#3E92D0",   # błękit
    "solid.jobs":   "#C566BE",   # magenta
    "indeed":       "#3FA9C4",   # cyjan
    "nofluffjobs":  "#7C93A8",   # stal
}

# Wpisy dodane ręcznie nie pochodzą z żadnego portalu - dostają barwę
# neutralną, żeby nie udawały dwunastego źródła.
SOURCE_FALLBACK = "#8A8F98"


def source_color(name) -> str:
    """
    Barwa kulki dla nazwy źródła.

    Normalizacja jest konieczna, bo ta sama nazwa przychodzi w kilku
    postaciach: baza ofert ma "Pracuj.pl", log pobrań "Pracuj.pl (Optimized
    APIs)", a lista `also_on` bywa bez ogonka. Ścinamy nawias i wielkość
    liter, a czego nie znamy - dostaje szarość, nie losowy kolor.
    """
    if not name:
        return SOURCE_FALLBACK
    key = str(name).split("(")[0].strip().lower()
    return SOURCES.get(key, SOURCE_FALLBACK)


def css_tokens() -> str:
    """Zmienne CSS dla arkusza aplikacji. Wstrzykiwane jako blok :root."""
    return ":root{" + "".join(f"--{k}:{v};" for k, v in TOKENS.items()) + "}"


def write_css_tokens(target: Path | None = None) -> Path:
    target = target or Path(__file__).parent / "frontend" / "src" / "theme_tokens.css"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(css_tokens() + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    path = write_css_tokens()
    print(f"zaktualizowano tokeny CSS {path}")
