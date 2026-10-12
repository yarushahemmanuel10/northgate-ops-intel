"""Name normalisation and comparison keys. Pure functions, no data access."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

_PUNCT = re.compile(r"[^\w\s]")


def norm(name: str, suffixes: list[str]) -> str:
    """Lower case, '&' -> 'and', punctuation removed, trailing legal suffixes dropped."""
    s = name.lower().replace("&", " and ")
    s = _PUNCT.sub(" ", s)
    words = s.split()
    changed = True
    while changed and words:
        changed = False
        if words[-1] in suffixes:
            words.pop()
            changed = True
        elif len(words) >= 2 and words[-2:] == ["and", "co"]:
            words = words[:-2]
            changed = True
    return " ".join(words)


def legal_form(name: str) -> str | None:
    """The legal form written in a name, if any: 'llp', 'ltd' or 'cio'."""
    low = _PUNCT.sub(" ", name.lower()).split()
    if not low:
        return None
    last = low[-1]
    if last == "llp":
        return "llp"
    if last in ("ltd", "limited"):
        return "ltd"
    if last == "cio":
        return "cio"
    return None


def billing_keys(name: str, length: int) -> set[str]:
    """How a billing system that drops the legal suffix and cuts names to `length` might write it."""
    base = name.strip()
    variants = {base}
    for suffix in (" Group Ltd", " & Co Ltd", " Ltd", " Limited", " LLP", " CIO"):
        if base.endswith(suffix):
            variants.add(base[: -len(suffix)])
    return {v.upper()[:length].rstrip() for v in variants}


def abbreviation_keys(normalised: str) -> set[str]:
    """Common shorthand: initials of the leading words plus the last word ('tv logistics'),
    or for two-word names the first word plus the start of the second ('stockton haul')."""
    words = [w for w in normalised.split() if w != "and"]
    keys = set()
    if len(words) >= 3:
        keys.add("".join(w[0] for w in words[:-1]) + " " + words[-1])
    if len(words) == 2 and len(words[1]) > 5:
        keys.add(f"{words[0]} {words[1][:4]}")
    return keys


def similarity(a: str, b: str) -> float:
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))
