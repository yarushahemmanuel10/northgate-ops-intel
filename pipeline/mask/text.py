"""Mask personal data in free text (step 6.1, D-043).

General rules only, nothing taken from the generator:
  EMAIL     any address of the form local@domain.tld
  PHONE     UK numbers in general: 0 or +44 followed by 9-10 more digits, with optional spaces, hyphens or
            brackets (01234 567890, 020 7946 0000, 07xxx xxx xxx, +44 (0)161 496 0000)
  NAME      (1) staff names from the warehouse's own lists (account managers, agents)
            (2) one to three capitalised words after a context cue ("contact", "spoke to", "email", "ask
                for", "reported by", a title such as "Mrs" ...), two words needed unless after a title
            (3) the person named by an email address of the form first.last@ or first_last@
            and every other occurrence of a name found in the same text
  CUSTOMER  business names from the warehouse's own lists (CRM names and review-queue spellings)

mask(text, lists) returns the masked text and the spans masked, so evaluation can score them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE = re.compile(r"(?<![\w+])(?:\+44\s?(?:\(0\)\s?)?|\(?0)\d[\d\s()-]{7,13}\d(?![\w])")
TOKEN = r"(?:Mc|Mac|O['’])?[A-Z][a-z]+(?:['’-][A-Z][a-z]+)*"     # Smith, McDonald, MacLeod, O'Brien, Smith-Jones
TITLES = r"Mr|Mrs|Ms|Miss|Mx|Dr|Prof"
CUES = (r"contact(?:ed)?|spoke (?:to|with)|speak (?:to|with)|talk(?:ed)? (?:to|with)|e-?mail(?:ed)?|"
        r"call(?:ed)?|ring|rang|phone(?:d)?|text(?:ed)?|ask(?:ed)? for|reported by|raised by|logged by|"
        r"on behalf of|for the attention of|attn:?|cc:?|dear|hi|hello|thanks|regards|cheers|"
        r"(?:user|caller|colleague|manager|contact|requester)(?: is| was|:)?")
CUED = re.compile(rf"(?i:\b(?:{CUES}))\s+(?:(?P<title>{TITLES})\.?\s+)?(?P<name>(?:{TOKEN})(?:\s+(?:{TOKEN})){{0,2}})")
TITLED = re.compile(rf"\b(?:{TITLES})\.?\s+(?P<name>(?:{TOKEN})(?:\s+(?:{TOKEN})){{0,2}})")
# Capitalised words that follow cues but are not people: products, teams and common words (general list).
NOT_NAMES = {w.lower() for w in """Windows Outlook Teams Microsoft Office Excel Word OneDrive SharePoint Google Chrome Edge
    Firefox Zoom Apple Mac Android Iphone Ipad Vpn Mfa Wifi Wi-Fi Internet Support Service Desk Helpdesk Help It Team
    Accounts Finance Reception Sales Admin Office Manager The This That User Users Customer Customers Engineer Engineers
    Northgate Please When If And Or Back Again Asap Today Tomorrow""".split()}
# Days and months: trimmed from the end of a name ("Contact Sam Singh Monday"), but allowed inside one, since
# April, May and June are also first names (D-043).
CALENDAR = {w.lower() for w in """Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May
    June July August September October November December""".split()}


@dataclass
class Lists:
    staff: set[str] = field(default_factory=set)        # person names from the warehouse (account managers, agents)
    customers: set[str] = field(default_factory=set)    # business names from the warehouse


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str          # NAME, PHONE, EMAIL, CUSTOMER
    rule: str


def _digits(s: str) -> str:
    """The number written as a UK national number: +44 (0)161 ... -> 0161 ..."""
    d = re.sub(r"\D", "", s)
    if s.lstrip().startswith("+44"):
        d = d[2:]
        d = "0" + (d[1:] if d.startswith("0") else d)
    return d


def _ok_name(name: str, titled: bool) -> str | None:
    words = name.split()
    while words and words[-1].lower() in NOT_NAMES | CALENDAR:  # "Contact Sam Singh Monday" -> "Sam Singh"
        words.pop()
    if not words or any(w.lower() in NOT_NAMES for w in words):
        return None
    if len(words) < 2 and not titled:
        return None
    return " ".join(words)


def find(text: str, lists: Lists) -> list[Span]:
    spans: list[Span] = []
    for m in EMAIL.finditer(text):
        spans.append(Span(m.start(), m.end(), "EMAIL", "email pattern"))
    for m in PHONE.finditer(text):
        if len(_digits(m.group(0))) in (10, 11) and _digits(m.group(0)).startswith("0"):
            spans.append(Span(m.start(), m.end(), "PHONE", "UK phone pattern"))
    names: dict[str, str] = {}
    for rx, rule in ((CUED, "context cue"), (TITLED, "title")):
        for m in rx.finditer(text):
            n = _ok_name(m.group("name"), titled=rule == "title" or bool(m.groupdict().get("title")))
            if n:
                names.setdefault(n, rule)
    for m in EMAIL.finditer(text):                                # first.last@ -> "First Last"
        local = m.group(0).split("@")[0]
        parts = re.split(r"[._]", local)
        if len(parts) == 2 and all(p.isalpha() and len(p) > 1 for p in parts):
            names.setdefault(" ".join(p.capitalize() for p in parts), "name in email address")
    for n in lists.staff:
        names.setdefault(n, "warehouse staff list")
    for n, rule in names.items():
        for m in re.finditer(rf"(?<![\w]){re.escape(n)}(?![\w])", text, flags=re.IGNORECASE):
            spans.append(Span(m.start(), m.end(), "NAME", rule))
    for c in lists.customers:
        for m in re.finditer(rf"(?<![\w]){re.escape(c)}(?![\w])", text, flags=re.IGNORECASE):
            spans.append(Span(m.start(), m.end(), "CUSTOMER", "warehouse customer list"))
    # keep the longest span where spans overlap (an email beats the name inside it)
    spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
    out: list[Span] = []
    for s in spans:
        if out and s.start < out[-1].end:
            continue
        out.append(s)
    return out


def mask(text: str | None, lists: Lists) -> tuple[str | None, list[Span]]:
    if not text:
        return text, []
    spans = find(text, lists)
    pieces, last = [], 0
    for s in spans:
        pieces += [text[last:s.start], f"[{s.kind}]"]
        last = s.end
    pieces.append(text[last:])
    return "".join(pieces), spans
