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


class _LostOutputTolerant:
    def __init__(self, stream):
        self._stream = stream
        self.lost = False

    def write(self, text):
        if not self.lost:
            try:
                return self._stream.write(text)
            except OSError:
                self.lost = True
        return len(text)

    def flush(self):
        if not self.lost:
            try:
                self._stream.flush()
            except OSError:
                self.lost = True

    def __getattr__(self, name):
        return getattr(self._stream, name)


def tolerate_lost_output() -> None:
    sys.stdout = _LostOutputTolerant(sys.stdout)
    sys.stderr = _LostOutputTolerant(sys.stderr)


def output_lost() -> bool:
    return any(getattr(stream, "lost", False) for stream in (sys.stdout, sys.stderr))
