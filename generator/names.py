"""Fictional company names and the name variants used to simulate messy source systems.

All randomness comes from a numpy Generator passed in by the caller (no global state).
Names are built from generic word lists; they are fictional and may coincidentally
resemble real businesses (D-006).
"""
from __future__ import annotations

import re

import numpy as np
from faker import Faker

PLACES = [
    "Tees Valley", "Stockton", "Darlington", "Hartlepool", "Redcar", "Yarm", "Thornaby",
    "Billingham", "Guisborough", "Saltburn", "Sedgefield", "Durham", "Newton Aycliffe",
    "Bishop Auckland", "Seaham", "Tyneside", "Wearside", "Teesside", "Cleveland",
    "North East", "Northern", "Riverside", "Eston", "Ingleby", "Marton", "Norton",
    "Ormesby", "Coulby", "Stokesley", "Spennymoor",
]

TRADES = {
    "logistics": ["Logistics", "Haulage", "Freight", "Transport", "Distribution", "Couriers"],
    "manufacturing": ["Engineering", "Fabrications", "Precision Components", "Plastics",
                      "Coatings", "Castings"],
    "professional services": ["Accountants", "Solicitors", "Surveyors", "Consulting",
                              "Architects", "Financial Planning"],
    "education": ["Training", "Learning Centre", "Day Nursery", "Tutoring",
                  "Driving School", "Skills Academy"],
    "healthcare": ["Dental Care", "Physiotherapy", "Opticians", "Veterinary Practice",
                   "Chiropody", "Hearing Care"],
    "retail": ["Furniture", "Garden Centre", "Cycles", "Interiors", "Hardware", "Bakery"],
    "charities": ["Community Trust", "Foundation", "Youth Project", "Arts Trust",
                  "Wildlife Trust", "Carers Network"],
    "construction": ["Construction", "Builders", "Roofing", "Scaffolding", "Groundworks",
                     "Electrical Contractors"],
}

# Look-alike pairs: two different companies whose names are easy to confuse. Each pair
# uses a different kind of confusion, so entity resolution is tested on each kind.
LOOKALIKE_KINDS = ("legal_form", "word_added", "surname", "trade_word")

LEGAL_FORM_TRADES = ["Accountants", "Solicitors", "Surveyors", "Architects"]   # professional services
WORD_ADDED = [  # (sector, shorter trade, longer trade)
    ("construction", "Roofing", "Roofing Supplies"),
    ("logistics", "Transport", "Transport Hire"),
    ("manufacturing", "Engineering", "Engineering Services"),
    ("healthcare", "Dental Care", "Dental Care Centre"),
]
NEAR_SURNAMES = [("Thompson", "Thomson"), ("Phillips", "Philips"), ("Clarke", "Clark"),
                 ("Stephenson", "Stevenson"), ("Matthews", "Mathews"), ("Reid", "Reed")]
NEAR_TRADES = [  # (sector, trade, near-identical trade)
    ("professional services", "Accountants", "Accountancy"),
    ("manufacturing", "Engineering", "Engineers"),
    ("construction", "Builders", "Building Services"),
    ("retail", "Interiors", "Interior Design"),
]

CHARITY_SUFFIXES = ["", "CIO"]
# "& Co" reads naturally after a surname in trades and firms, not after e.g. "Day Nursery".
CO_SECTORS = {"professional services", "construction", "manufacturing", "logistics"}
LEGAL_SUFFIX_RE = re.compile(r"\s+(group ltd|& co ltd|ltd|limited|llp|cio)\.?$", re.IGNORECASE)

TELECOMS_FIELD_LENGTH = 20


def core_name(name: str) -> str:
    """Name without legal suffix, lower case, single spaces. Used for uniqueness checks."""
    return " ".join(LEGAL_SUFFIX_RE.sub("", name.strip()).lower().split())


def is_blocked(name: str, blocklist: list[str]) -> bool:
    low = name.lower()
    return any(token.lower() in low for token in blocklist)


def _pick(options, rng: np.random.Generator):
    return options[int(rng.integers(len(options)))]


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def make_name(sector: str, rng: np.random.Generator, fake: Faker) -> str:
    """One fictional company name for a sector.

    Patterns: place + trade (about 65%), surname + trade (20%), two surnames + trade (10%),
    place + trade + "Group" (5%). "& Co" is only used after a surname, in CO_SECTORS.
    """
    trade = str(_pick(TRADES[sector], rng))
    place = str(_pick(PLACES, rng))
    if sector == "charities":
        return _join(place, trade, str(_pick(CHARITY_SUFFIXES, rng)))
    pattern = rng.choice(["place", "surname", "partners", "group"], p=[0.65, 0.20, 0.10, 0.05])
    if pattern == "surname":
        suffixes = ["Ltd", "Ltd", "Limited"] + (["& Co Ltd"] if sector in CO_SECTORS else [])
        return _join(fake.last_name(), trade, str(_pick(suffixes, rng)))
    if pattern == "partners":
        legal = "LLP" if sector == "professional services" else "Ltd"
        return _join(f"{fake.last_name()} & {fake.last_name()}", trade, legal)
    if pattern == "group":
        return _join(place, trade, "Group Ltd")
    return _join(place, trade, str(_pick(["Ltd", "Ltd", "Limited"], rng)))


def make_lookalike_pair(kind: str, rng: np.random.Generator, fake: Faker) -> tuple[str, str, str]:
    """Two different companies with confusingly similar names, and their shared sector."""
    place = str(_pick(PLACES, rng))
    if kind == "legal_form":       # only the legal form differs
        base = _join(f"{fake.last_name()} & {fake.last_name()}", str(_pick(LEGAL_FORM_TRADES, rng)))
        return f"{base} LLP", f"{base} Ltd", "professional services"
    if kind == "word_added":       # a word added or dropped
        sector, short, long = _pick(WORD_ADDED, rng)
        return _join(place, short, "Ltd"), _join(place, long, "Ltd"), sector
    if kind == "surname":          # near-identical surnames, same trade
        a, b = _pick(NEAR_SURNAMES, rng)
        sector = str(_pick(["construction", "logistics", "manufacturing"], rng))
        trade = str(_pick(TRADES[sector], rng))
        return _join(a, trade, "Ltd"), _join(b, trade, "Ltd"), sector
    if kind == "trade_word":       # near-identical trade words, same place
        sector, t1, t2 = _pick(NEAR_TRADES, rng)
        return _join(place, t1, "Ltd"), _join(place, t2, "Ltd"), sector
    raise ValueError(f"unknown look-alike kind: {kind}")


# --- name variants (QI-01) ---------------------------------------------------

VARIANT_KINDS = ("abbreviation", "lowercase", "drop_suffix", "typo", "uppercase_truncate")


def _core_words(name: str) -> list[str]:
    return [w for w in LEGAL_SUFFIX_RE.sub("", name.strip()).split() if w != "&"]


def abbreviate(name: str) -> str:
    """'Tees Valley Logistics Ltd' -> 'TV Logistics'; 'Stockton Haulage Ltd' -> 'Stockton Haul.'"""
    words = _core_words(name)
    if len(words) >= 3:
        return "".join(w[0].upper() for w in words[:-1]) + " " + words[-1]
    if len(words) == 2 and len(words[1]) > 5:
        return f"{words[0]} {words[1][:4]}."
    return "".join(w[0].upper() for w in words)


def typo(name: str, rng: np.random.Generator) -> str:
    """One character-level edit (swap, drop or double) in a word of 4+ letters."""
    words = LEGAL_SUFFIX_RE.sub("", name.strip()).split()
    suffix = name.strip()[len(" ".join(words)):]
    candidates = [i for i, w in enumerate(words) if len(w) >= 4 and w.isalpha()]
    if not candidates:
        return name + "s"
    i = candidates[rng.integers(len(candidates))]
    w = words[i]
    j = int(rng.integers(1, len(w) - 1))
    edit = rng.choice(["swap", "drop", "double"])
    if edit == "swap" and w[j] != w[j + 1]:
        w = w[:j] + w[j + 1] + w[j] + w[j + 2:]
    elif edit == "drop":
        w = w[:j] + w[j + 1:]
    else:
        w = w[:j] + w[j] + w[j:]
    words[i] = w
    return " ".join(words) + suffix


def name_variant(name: str, kind: str, rng: np.random.Generator) -> str:
    """Return `name` written the way a particular system or person might write it."""
    if kind == "abbreviation":
        out = abbreviate(name)
    elif kind == "lowercase":
        out = name.lower()
    elif kind == "drop_suffix":
        out = LEGAL_SUFFIX_RE.sub("", name.strip())
    elif kind == "typo":
        out = typo(name, rng)
    elif kind == "uppercase_truncate":
        out = LEGAL_SUFFIX_RE.sub("", name.strip()).upper()[:TELECOMS_FIELD_LENGTH].rstrip()
    else:
        raise ValueError(f"unknown variant kind: {kind}")
    if out == name:  # e.g. a charity with no suffix under drop_suffix
        out = name.lower() if kind != "lowercase" else name.upper()
    return out
