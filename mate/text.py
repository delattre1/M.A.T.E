"""Name matching for data typed by thousands of different people.

OSM has "petropolis", "Petrópolis" and "PETROPOLIS" for the same town, and two
separate nodes for "Cachoeira Véu de Noiva" and "Cachoeira Veu de Noiva".
"""

from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def fold(s: str) -> str:
    stripped = unicodedata.normalize("NFKD", s or "")
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return _SPACE.sub(" ", _PUNCT.sub(" ", stripped.casefold())).strip()


def same_place(a: str, b: str) -> bool:
    return bool(a) and fold(a) == fold(b)
