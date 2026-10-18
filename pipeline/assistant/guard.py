"""What may leave the machine (D-037).

Only knowledge-base section text, aggregated tool results and the question go to the Claude API. Before any
request is sent, check_outgoing() searches the whole request for every business name, review-queue name,
staff name and ticket text held in the local warehouse, and refuses to send if one is found. Questions are
first passed through names_to_ids(), so a user who types a business name sends its customer ID instead.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

MIN_TEXT = 25          # ticket text shorter than this (e.g. "Printer offline") is too generic to identify anyone


class LeakBlocked(Exception):
    """A request held something that must not leave the machine. The message never repeats it."""


@dataclass(frozen=True)
class Forbidden:
    names: dict[str, str]          # lower-case business name -> customer ID ('' for names not linked to one)
    staff: frozenset[str]
    ticket_text: frozenset[str]

    @classmethod
    def load(cls, warehouse_db: Path) -> "Forbidden":
        con = duckdb.connect(str(warehouse_db), read_only=True)
        try:
            names = {n.lower(): c for c, n in con.execute(
                "SELECT customer_id, company_name FROM dim_customer WHERE company_name IS NOT NULL AND NOT is_unknown").fetchall()}
            for (n,) in con.execute("SELECT DISTINCT name_as_written FROM v_kpi_bq14_review_queue").fetchall():
                names.setdefault(n.lower(), "")
            staff = {s.lower() for (s,) in con.execute(
                "SELECT DISTINCT account_manager FROM dim_customer WHERE account_manager IS NOT NULL").fetchall()}
            text = {t.lower() for row in con.execute("SELECT description, resolution_notes FROM ticket_text").fetchall()
                    for t in row if t and len(t) >= MIN_TEXT}
        finally:
            con.close()
        return cls(names, frozenset(staff), frozenset(text))


def names_to_ids(question: str, forbidden: Forbidden) -> str:
    """Replace business names the user typed with their customer IDs, longest name first."""
    out = question
    for name in sorted(forbidden.names, key=len, reverse=True):
        cid = forbidden.names[name] or "a customer"
        out = re.sub(re.escape(name), cid, out, flags=re.IGNORECASE)
    return out


def check_outgoing(request: dict, forbidden: Forbidden) -> None:
    """Raise LeakBlocked if any forbidden value appears anywhere in the request."""
    blob = json.dumps(request, ensure_ascii=False).lower()
    kinds = (("a business name", forbidden.names), ("a staff name", forbidden.staff),
             ("ticket text", forbidden.ticket_text))
    for kind, values in kinds:
        if any(v in blob for v in values):
            raise LeakBlocked(f"Request not sent: it contained {kind}. Nothing left the machine.")
