"""Parsers for dates, money and priorities. Each returns (value or None, action, detail).

Actions: "ok" (already standard), "converted" (non-standard text matched a documented
rule), "converted_by_convention" (a day-first numeric date with day <= 12, converted by the
source's documented convention), "normalised" (case only), "flagged" (no rule matched; the
typed value is empty and the original text is kept).
"""
from __future__ import annotations

import datetime as dt
import re

MONEY_RE = re.compile(r"^(?:£)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(k)?(?: GBP)?$")


def parse_date(text: str, formats: list[str], plausible: tuple[dt.date, dt.date],
               timestamp: bool = False):
    text = text.strip()
    if not text:
        return None, "empty", ""
    for i, fmt in enumerate(formats):
        try:
            value = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        d = value.date()
        if not plausible[0] <= d <= plausible[1]:
            return None, "flagged", f"date {d} outside the plausible range"
        out = value if timestamp else d
        if i == 0:
            return out, "ok", ""
        if fmt.startswith("%d/%m") and value.day <= 12 and value.month != value.day:
            return out, "converted_by_convention", f"ambiguous {text!r} read day-first ({fmt})"
        return out, "converted", f"{text!r} matched {fmt}"
    return None, "flagged", f"{text!r} matches no documented format"


def parse_money(text: str, allow_k: bool = True):
    t = text.strip()
    if not t:
        return None, "empty", ""
    m = MONEY_RE.match(t)
    if not m or (m.group(3) and not allow_k):
        return None, "flagged", f"{text!r} is not a recognised amount"
    number = float(m.group(1).replace(",", "") + (m.group(2) or ""))
    if m.group(3):
        number *= 1000
    number = round(number, 2)
    standard = re.fullmatch(r"\d+", t) is not None
    return number, ("ok" if standard else "converted"), "" if standard else f"{text!r} read as {number:g}"


def parse_priority(text: str, valid: list[str], case_insensitive: bool, mapping: dict):
    t = text.strip()
    if t in valid:
        return t, "ok", ""
    if case_insensitive and t.upper() in valid:
        return t.upper(), "normalised", f"{text!r} upper-cased"
    if t in mapping:
        return mapping[t], "converted", f"{text!r} mapped by validation.yaml"
    return None, "flagged", f"{text!r} is not a valid priority"


def parse_number(text: str):
    try:
        return float(text), "ok", ""
    except ValueError:
        return None, "flagged", f"{text!r} is not a number"
