"""Tokenising for keyword search (step 5.2, D-035). General rules only, fixed before any
evaluation question was read: lower case, split on non-alphanumerics, drop one-character tokens
and common English stop words, light suffix stripping. No synonym list."""
from __future__ import annotations

import re

# A general English stop-word list (articles, pronouns, auxiliaries, prepositions, conjunctions).
STOP_WORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers herself him himself his how i if in into is it its itself just me more most
my myself no nor not of off on once only or other our ours ourselves out over own same she should so
some such than that the their theirs them themselves then there these they this those through to too
under until up very was we were what when where which while who whom why will with would you your
yours yourself yourselves
""".split())
_TOKEN = re.compile(r"[a-z0-9]+")


def stem(word: str) -> str:
    """Light suffix stripping, the same for documents and queries."""
    if word.endswith("'s"):
        word = word[:-2]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    for suffix, min_len in (("ing", 6), ("ed", 5)):
        if len(word) >= min_len and word.endswith(suffix):
            return word[: -len(suffix)]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    return [stem(t) for t in _TOKEN.findall(text.lower()) if len(t) > 1 and t not in STOP_WORDS]
