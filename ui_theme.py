"""
Paleta i kroje pisma aplikacji - jedno miejsce prawdy.

Kolory żyły wcześniej w dwóch miejscach naraz: `.streamlit/config.toml`
(bo Streamlit czyta motyw przed startem aplikacji) i w CSS wstrzykiwanym
z kodu. Przy każdej zmianie trzeba było pamiętać o obu, a rozjazd nie dawał
żadnego błędu - po prostu połowa interfejsu zostawała w starym kolorze.

Teraz schemat siedzi tutaj, a `config.toml` jest z niego generowany:

    python ui_theme.py            # przepisuje .streamlit/config.toml
    python ui_theme.py --list     # pokazuje dostępne warianty

Po zmianie schematu trzeba zrestartować Streamlita - motyw natywny czytany
jest raz, przy starcie.
"""
from dataclasses import dataclass
from pathlib import Path

# --- wybór aktywnego wariantu -------------------------------------------
ACTIVE_SCHEME = "grafit"
ACTIVE_FONTS = "redakcja"


@dataclass(frozen=True)
class Scheme:
    label: str
    # podłoże
    ground: str
    raised: str
    raised_hi: str
    line: str
    line_soft: str
    sidebar_bg: str
    sidebar_raised: str
    # tekst
    text: str
    text_bright: str
    muted: str
    faint: str
    # akcent - niesie wyłącznie procent dopasowania
    accent: str        # najwyższe dopasowanie
    accent_dim: str    # średnie
    accent_ash: str    # niskie
    accent_soft: str   # tło kafla i werdyktu AI
    accent_edge: str   # obrys kafla


@dataclass(frozen=True)
class FontSet:
    label: str
    heading: str
    body: str
    mono: str
    heading_url: str
    body_url: str
    mono_url: str
    heading_stretch: str = "100%"


# Barwy stanów decyzji są wspólne dla wszystkich schematów - to one budują
# pamięć wzrokową ("zielone = wysłane") i nie powinny się zmieniać razem
# z akcentem. Każdy akcent musi być od nich odsunięty na kole barw, inaczej
# ikona decyzji zlewa się z metryką dopasowania.
STATES = {
    "moss": "#7FB069",   # wysłane
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


SCHEMES = {
    # Bez akcentu barwnego. Dopasowanie idzie jasnością, nie kolorem, więc
    # jedynymi barwnymi punktami na ekranie zostają ikony decyzji.
    "grafit": Scheme(
        label="Grafit - dopasowanie jasnością, kolor tylko na decyzjach",
        ground="#131417", raised="#1B1D21", raised_hi="#22252A",
        line="#2E3238", line_soft="#24272C",
        sidebar_bg="#0F1013", sidebar_raised="#191B1F",
        text="#E8EAED", text_bright="#FBFCFD", muted="#949AA3", faint="#646A73",
        accent="#E6EAF0", accent_dim="#9BA3AE", accent_ash="#5A6068",
        accent_soft="rgba(230, 234, 240, 0.07)",
        accent_edge="rgba(230, 234, 240, 0.22)",
    ),
    "fiolet": Scheme(
        label="Fiolet - chłodny grafit, akcent indygo",
        ground="#121218", raised="#1B1B24", raised_hi="#22222D",
        line="#2F2F3B", line_soft="#25252F",
        sidebar_bg="#0E0E13", sidebar_raised="#191921",
        text="#EAE9F2", text_bright="#FAF9FE", muted="#918FA6", faint="#63617A",
        accent="#A78BFA", accent_dim="#7C6BC4", accent_ash="#514E68",
        accent_soft="rgba(167, 139, 250, 0.09)",
        accent_edge="rgba(167, 139, 250, 0.30)",
    ),
    "malina": Scheme(
        label="Malina - ciepły grafit, akcent koralowo-różowy",
        ground="#161316", raised="#201B1F", raised_hi="#282126",
        line="#382E35", line_soft="#2B242A",
        sidebar_bg="#120F12", sidebar_raised="#1D181C",
        text="#F0E9EE", text_bright="#FDF8FB", muted="#A2929C", faint="#6F626B",
        accent="#E8798F", accent_dim="#B25C6E", accent_ash="#5F4A52",
        accent_soft="rgba(232, 121, 143, 0.09)",
        accent_edge="rgba(232, 121, 143, 0.30)",
    ),
}


FONTS = {
    # Zestaw domyślny. Poprzedni (archivo) miał jeden problem: Archivo jest
    # grotestkiem o kwadratowych światłach, a rozciągnięcie go do wdth 112%
    # dokwadratowiało go jeszcze bardziej. Do tego IBM Plex Mono szedł na
    # wszystko - markę, zakładki, tytuły paneli, etykiety - więc cały układ
    # mówił jednym, technicznym głosem i hierarchia się nie budowała.
    #
    # Tutaj każdy krój ma jedną roboto:
    #   Fraunces  - marka, tytuły paneli i liczby. Szeryf o miękkich łukach,
    #               osobny ton, którego nie da się pomylić z resztą.
    #   Manrope   - interfejs i treść. Ciepła, okrągła geometria.
    #   Fira Mono - WYŁĄCZNIE dane: znaczniki czasu, liczniki, procenty.
    "redakcja": FontSet(
        label="Fraunces + Manrope - szeryfowy ton na nagłówkach, mono tylko na danych",
        heading="Fraunces", body="Manrope", mono="Fira Mono",
        heading_stretch="100%",
        heading_url="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400..700&display=swap",
        body_url="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&display=swap",
        mono_url="https://fonts.googleapis.com/css2?family=Fira+Mono:wght@400;500;700&display=swap",
    ),
    "archivo": FontSet(
        label="Archivo + IBM Plex - wąskie, techniczne nagłówki",
        heading="Archivo", body="IBM Plex Sans", mono="IBM Plex Mono",
        heading_stretch="112%",
        heading_url="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@100..125,400..700&display=swap",
        body_url="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap",
        mono_url="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap",
    ),
    "sora": FontSet(
        label="Sora + Public Sans - geometryczne, produktowe",
        heading="Sora", body="Public Sans", mono="JetBrains Mono",
        heading_url="https://fonts.googleapis.com/css2?family=Sora:wght@400..700&display=swap",
        body_url="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&display=swap",
        mono_url="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap",
    ),
    "manrope": FontSet(
        label="Bricolage + Manrope - miękkie, o wyraźnym charakterze",
        heading="Bricolage Grotesque", body="Manrope", mono="IBM Plex Mono",
        heading_url="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400..700&display=swap",
        body_url="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&display=swap",
        mono_url="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap",
    ),
}


def scheme() -> Scheme:
    return SCHEMES[ACTIVE_SCHEME]


def fonts() -> FontSet:
    return FONTS[ACTIVE_FONTS]


def css_tokens() -> str:
    """Zmienne CSS dla arkusza aplikacji. Wstrzykiwane jako blok :root."""
    s, f = scheme(), fonts()
    pairs = {
        "--ground": s.ground, "--raised": s.raised, "--raised-hi": s.raised_hi,
        "--line": s.line, "--line-soft": s.line_soft,
        "--text": s.text, "--text-bright": s.text_bright,
        "--muted": s.muted, "--faint": s.faint,
        "--accent": s.accent, "--accent-dim": s.accent_dim,
        "--accent-ash": s.accent_ash,
        "--accent-soft": s.accent_soft, "--accent-edge": s.accent_edge,
        "--moss": STATES["moss"], "--slate": STATES["slate"],
        "--amber": STATES["amber"], "--clay": STATES["clay"],
        "--font-head": f"'{f.heading}', sans-serif",
        "--font-body": f"'{f.body}', sans-serif",
        "--font-mono": f"'{f.mono}', monospace",
        "--head-stretch": f.heading_stretch,
    }
    return ":root{" + "".join(f"{k}:{v};" for k, v in pairs.items()) + "}"


def score_bands():
    """(kolor, tło, obrys) dla wysokiego / średniego / niskiego dopasowania."""
    s = scheme()
    return (
        (s.accent, s.accent_soft, s.accent_edge),
        (s.accent_dim, s.accent_soft, s.accent_edge),
        (s.accent_ash, "rgba(255, 255, 255, 0.03)", s.line),
    )


def config_toml() -> str:
    s, f = scheme(), fonts()
    return f"""# PLIK GENEROWANY - nie edytuj ręcznie.
# Źródłem jest ui_theme.py; po zmianie uruchom `python ui_theme.py`
# i zrestartuj Streamlita (motyw natywny czytany jest raz, przy starcie).
#
# Wariant koloru: {ACTIVE_SCHEME} - {s.label}
# Wariant pisma : {ACTIVE_FONTS} - {f.label}

[theme]
base = "dark"

backgroundColor          = "{s.ground}"
secondaryBackgroundColor = "{s.raised}"
textColor                = "{s.text}"
borderColor              = "{s.line}"

primaryColor = "{s.accent}"
linkColor    = "{s.accent}"

greenColor  = "{STATES['moss']}"
blueColor   = "{STATES['slate']}"
yellowColor = "{STATES['amber']}"
redColor    = "{STATES['clay']}"
grayColor   = "{s.muted}"

baseRadius       = "0.6rem"
buttonRadius     = "0.45rem"
showWidgetBorder = true
baseFontSize     = 15

headingFont = "{f.heading}:{f.heading_url}"
font        = "{f.body}:{f.body_url}"
codeFont    = "{f.mono}:{f.mono_url}"

[theme.sidebar]
backgroundColor          = "{s.sidebar_bg}"
secondaryBackgroundColor = "{s.sidebar_raised}"
borderColor              = "{s.line_soft}"
textColor                = "{s.muted}"
"""


def write_config(target: Path | None = None) -> Path:
    target = target or Path(__file__).parent / ".streamlit" / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(config_toml(), encoding="utf-8")
    return target


if __name__ == "__main__":
    import sys

    if "--list" in sys.argv:
        print("Kolory:")
        for key, val in SCHEMES.items():
            mark = "*" if key == ACTIVE_SCHEME else " "
            print(f" {mark} {key:10} {val.label}")
        print("\nPismo:")
        for key, val in FONTS.items():
            mark = "*" if key == ACTIVE_FONTS else " "
            print(f" {mark} {key:10} {val.label}")
    else:
        path = write_config()
        print(f"zapisano {path}  [{ACTIVE_SCHEME} / {ACTIVE_FONTS}]")
