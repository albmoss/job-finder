"""
Paleta i kroje pisma aplikacji - jedno miejsce prawdy.

Tokeny (ciemny motyw) backend podaje w bootstrap API, frontend czyta z `theme_tokens.css`.

    python ui_theme.py            # generuje frontend/src/theme_tokens.css
"""
from pathlib import Path

# Kolejność = kolejność w wygenerowanym pliku.
TOKENS = {
    # powierzchnie
    "bg": "#0E0E10",
    "panel": "rgba(24, 24, 26, 0.92)",
    "paper": "#F2F2F0",
    "scrim": "rgba(8, 8, 9, 0.6)",
    # tusz
    "ink": "#F5F5F4",
    "muted": "#A3A3A3",
    "faint": "#6E6E72",
    "accent": "#E7E5E4",
    # linie
    "line": "rgba(255, 255, 255, 0.09)",
    "track": "#5E5E62",
    # liczby do sprawdzenia, ikona błędu
    "warn": "#E8C27A",
    "warn-fill": "rgba(232, 194, 122, 0.08)",
    "warn-line": "rgba(232, 194, 122, 0.35)",
    # pismo
    "font": "'Geist', system-ui, -apple-system, 'Segoe UI', sans-serif",
    # ruch
    "ease-out": "cubic-bezier(0.23, 1, 0.32, 1)",
    "ease-in-out": "cubic-bezier(0.77, 0, 0.175, 1)",
}


# Barwa portalu. Kulka przy nazwie źródła jest w aplikacji wszędzie ta sama:
# w wierszu oferty, w szczegółach, na karcie i w logu pobrań - dzięki temu
# "skąd to jest" czyta się jednym rzutem oka, bez czytania nazwy.
#
# Dobór barw: żadnego żółtego.
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
