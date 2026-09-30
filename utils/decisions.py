"""
Wspólna reguła odczytu decyzji dla ewaluacji rankingu.

Decyzja w `user_decisions.json` to dawny string ("reject" z masowego czyszczenia)
albo słownik ze `status` i `rating`. Tu żyje jedna odpowiedź na pytanie, ile znaczy
decyzja bez oceny liczbowej.
"""

from typing import Optional

# „Zapisz” i „Wysłane” bez suwaka to wyraźne zainteresowanie, „Odrzuć” - wyraźna odmowa.
# Pozostałe statusy bez liczby (np. aspiracje) nie mówią, jak wysoko stawiasz ofertę.
IMPLIED_RATING = {"save": 9, "apply": 9, "reject": 1}


def effective_rating(decision: dict) -> Optional[int]:
    """Ocena 1-10 wystawiona albo wynikająca ze statusu; None, gdy decyzja jej nie niesie."""
    rating = decision.get("rating")
    if rating is not None:
        return int(rating)
    return IMPLIED_RATING.get(decision.get("status"))

