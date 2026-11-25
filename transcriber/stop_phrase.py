"""Reconnaît une phrase d'arrêt (STOP_PHRASE) à la fin d'une prise de parole transcrite.

STOP_PHRASE peut contenir plusieurs phrases séparées par « | » : n'importe laquelle arrête le meet.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Whisper fait de petites erreurs (« Clode », mot coupé en deux) : on tolère un écart.
SIMILARITY = 0.9
# La phrase doit arriver en fin de prise de parole, à quelques mots près (« …les travaux, à plus »),
# pour ne pas couper le meet quand quelqu'un se contente d'en parler au milieu d'une phrase.
TRAILING_WORDS = 5


def normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).split()


def ends_with_stop_phrase(text: str, phrase: str) -> bool:
    target = normalize(phrase)
    if not target:
        return False
    words = normalize(text)
    tail = words[-(len(target) + TRAILING_WORDS) :]
    goal = " ".join(target)
    # Fenêtres d'un mot de plus ou de moins que la phrase, pour les mots fusionnés ou coupés.
    for size in {max(1, len(target) - 1), len(target), len(target) + 1}:
        for start in range(0, max(1, len(tail) - size + 1)):
            window = " ".join(tail[start : start + size])
            if SequenceMatcher(None, window, goal).ratio() >= SIMILARITY:
                return True
    return False


def parse_stop_phrases(value: str | None) -> list[str]:
    """« merci Claude pour les travaux | Claude, coupe l'enregistrement » → les phrases, sans les vides."""
    return [p.strip() for p in (value or "").split("|") if p.strip()]


def matching_stop_phrase(text: str, phrases: list[str]) -> str | None:
    """La première phrase d'arrêt par laquelle se termine la prise de parole, sinon None."""
    for phrase in phrases:
        if ends_with_stop_phrase(text, phrase):
            return phrase
    return None
