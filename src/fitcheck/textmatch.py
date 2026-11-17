"""One definition of "verbatim" for the whole project (decision_rules.md R4).

Line breaks, extra spaces, and capitalization don't change meaning, and neither do typographic
variants a model tends to retype as plain characters: curly quotes, en/em dashes, the ellipsis
character, and non-breaking spaces. Everything else must match. Normalizing is for comparison
only: quotes are always stored and shown exactly as written.
"""

import re

# Typographic character -> the plain character a model types instead.
_PLAIN = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",  # ‘ ’ ‚ ‛ ′
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',  # “ ” „ ‟ ″
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",  # ‐ ‑ ‒ – — ―
    "…": "...",  # …
    " ": " ", " ": " ", " ": " ",  # non-breaking spaces
})


def normalize(text: str) -> str:
    return " ".join(text.translate(_PLAIN).split()).casefold()


def appears_in(needle: str, haystack: str) -> bool:
    return normalize(needle) in normalize(haystack)


def names_term(text: str, term: str) -> bool:
    """True if `term` appears in `text` as a whole word: "go" is not found inside "good"."""
    pattern = rf"(?<![a-z0-9]){re.escape(normalize(term))}(?![a-z0-9])"
    return re.search(pattern, normalize(text)) is not None
