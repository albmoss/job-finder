"""
Wymuszenie UTF-8 na konsoli.

Skrypty wypisują znaki spoza cp1252 (✓, ✗, →, ramki). Domyślna konsola Windows
używa cp1252 i `print` rzuca wtedy UnicodeEncodeError - nie na starcie, tylko
przy pierwszym takim znaku, czyli zwykle w podsumowaniu na końcu etapu.
Pipeline zdążył już zapisać dane, ale przewracał się przed kolejną fazą.

Wywoływane raz, w punkcie wejścia; reconfigure działa na cały proces.
"""

import sys


def force_utf8(errors: str = "replace") -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors=errors)
        except (AttributeError, ValueError):
            # Strumień przekierowany do obiektu bez reconfigure - nie ma czego psuć.
            pass
