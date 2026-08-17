"""
Kanoniczna postać linku do oferty.

Link jest w tym systemie kluczem głównym - łączy bazę ofert, wyniki analizy AI
i decyzje użytkownika. Jeśli te trzy pliki użyją różnych wariantów tego samego
URL-a, oceny "odklejają się" od ofert (OLX doklejał ?search_reason=search|organic,
przez co ta sama oferta figurowała wielokrotnie).

Każdy zapis linku powinien przechodzić przez canonical_link().
"""

# Parametry, które są częścią tożsamości oferty i NIE mogą zostać ucięte.
# "jk=" to identyfikator oferty na Indeed (/viewjob?jk=...) - bez niego wszystkie
# oferty z tego portalu sprowadzałyby się do jednego klucza ".../viewjob"
# i zostawała by w bazie dokładnie jedna z nich.
_MEANINGFUL_PARAMS = ("id=", "offerId=", "jobId=", "oid=", "jk=")


def canonical_link(link: str) -> str:
    """
    Sprowadź URL oferty do postaci kanonicznej:
    usuń fragment (#...), parametry śledzące i końcowy slash.

    Query string jest zachowywany tylko wtedy, gdy niesie identyfikator oferty -
    inaczej straciłoby się rozróżnienie między ofertami na portalach, które
    trzymają ID w query.
    """
    if not link:
        return link

    link = link.split("#")[0].strip()

    if "?" in link:
        base, _, query = link.partition("?")
        if any(p in query for p in _MEANINGFUL_PARAMS):
            return link.rstrip("/")
        link = base

    return link.rstrip("/")
