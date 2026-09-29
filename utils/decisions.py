"""
Wspólne reguły odczytu decyzji dla profilu preferencji, ewaluacji rankingu i serwera.

Decyzja w `user_decisions.json` to dawny string ("reject" z masowego czyszczenia)
albo słownik ze `status` i `rating`. Tu żyje jedna odpowiedź na dwa pytania:
ile znaczy decyzja bez oceny liczbowej i czy profil widział bieżące decyzje.
"""

from datetime import datetime
from typing import Optional

from utils.links import canonical_link

# „Zapisz” i „Wysłane” bez suwaka to wyraźne zainteresowanie, „Odrzuć” - wyraźna odmowa.
# Pozostałe statusy bez liczby (np. aspiracje) nie mówią, jak wysoko stawiasz ofertę.
IMPLIED_RATING = {"save": 9, "apply": 9, "reject": 1}


def effective_rating(decision: dict) -> Optional[int]:
    """Ocena 1-10 wystawiona albo wynikająca ze statusu; None, gdy decyzja jej nie niesie."""
    rating = decision.get("rating")
    if rating is not None:
        return int(rating)
    return IMPLIED_RATING.get(decision.get("status"))


def decision_snapshot(decisions: dict) -> dict:
    """{link kanoniczny: [status, ocena]} decyzji w nowym formacie - to, co czyta profil."""
    return {
        canonical_link(link): [val.get("status"), val.get("rating")]
        for link, val in decisions.items()
        if isinstance(val, dict)
    }


def decisions_since_profile(decisions: dict, metadata: dict) -> int:
    """
    Ile decyzji profil jeszcze nie uwzględnia: nowe, zmienione i cofnięte.

    Profil zapisuje w `_metadata.decisions` stan, z którego powstał. Profil
    sprzed tego pola porównujemy po `decided_at` z datą wygenerowania.
    """
    seen = metadata.get("decisions")
    if isinstance(seen, dict):
        current = decision_snapshot(decisions)
        changed = sum(1 for link, value in current.items() if seen.get(link) != value)
        return changed + sum(1 for link in seen if link not in current)

    try:
        generated = datetime.fromisoformat(metadata["generated_at"])
    except (KeyError, TypeError, ValueError):
        return 0
    count = 0
    for val in decisions.values():
        if not isinstance(val, dict) or not val.get("decided_at"):
            continue
        try:
            if datetime.strptime(val["decided_at"][:16], "%Y-%m-%d %H:%M") > generated:
                count += 1
        except ValueError:
            continue
    return count
