"""Review sheet for the queued records (step 4.2, D-029).

Run:  python -m pipeline.warehouse.review_sheet   (after pipeline.resolve.run)

Writes data/warehouse/review_sheet.csv: one row per queue item, and for items whose spelling is
too close between candidates (e.g. "RP Solicitors" for both LA-1 companies) one extra row per
record, so each can be decided on its own. The first six columns are the columns of
config/review_decisions.csv: fill in decided_account_id, reviewer, decided_on and reason for the
rows you decide, and copy those rows there. Leave decided_account_id blank to leave a record in
the queue. Nothing here is filled in for you.

Evidence comes from data/validated/ only (never the answer key): the top three candidate
customers and their scores; for helpdesk records the ticket date, priority, category and any
email *domain* in the ticket text (never the address or a person's name); for shop accounts the
order dates, included-order total (marked duplicates left out, as in the warehouse) and every
company name exactly as written.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from pipeline.ingest.common import write_staging
from pipeline.resolve.run import Resolver, load_config as resolution_config
from pipeline.settings import load_settings
from pipeline.warehouse.load import DECISION_COLUMNS

EMAIL_DOMAIN = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
SHEET_COLUMNS = DECISION_COLUMNS + [
    "level", "queue_item", "records", "name_as_written", "queue_reason",
    "candidate_1", "candidate_1_name", "score_1", "candidate_2", "candidate_2_name", "score_2",
    "candidate_3", "candidate_3_name", "score_3",
    "email_domains", "first_date", "last_date", "orders_total", "priority_category", "names_as_written"]


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def email_domains(*texts: str) -> list[str]:
    """Domains only: the part after @, lower case. The address itself is never returned."""
    return sorted({d.lower() for t in texts for d in EMAIL_DOMAIN.findall(t or "")})


def build_sheet(validated: Path, resolved: Path, duplicates: Path | None = None) -> list[dict]:
    accounts = _read(validated / "crm_accounts.csv")
    names = {a["Account ID"]: a["Company Name"] for a in accounts}
    resolver = Resolver(resolution_config(), accounts, _read(validated / "crm_contract_lines.csv"))
    queue = _read(resolved / "review_queue.csv")
    crosswalk = [r for r in _read(resolved / "crosswalk.csv") if r["status"] == "review"]
    tickets = {t["ticket_id"]: t for t in _read(validated / "helpdesk_tickets.csv")}
    dup_orders = {d["record_key"] for d in (_read(duplicates) if duplicates and duplicates.exists() else [])
                  if d["table"] == "shop_orders"}
    orders = [o for o in _read(validated / "shop_orders.csv") if o["order_id"] not in dup_orders]

    def candidates(raw: str) -> dict:
        out = {}
        for i, (acc, sc, _) in enumerate(resolver.score_name(raw)[:3], start=1):
            out.update({f"candidate_{i}": acc, f"candidate_{i}_name": names[acc], f"score_{i}": f"{sc:.1f}"})
        return out

    def blank(system: str, key: str) -> dict:
        return {"source_system": system, "source_key": key, "decided_account_id": "", "reviewer": "",
                "decided_on": "", "reason": ""}

    rows = []
    for q in queue:
        system, unit, raw = q["source_system"], q["unit"], q["raw_name"]
        item = {**blank(system, unit), "level": "item", "queue_item": unit, "records": q["records"],
                "name_as_written": raw, "queue_reason": q["reason"], **candidates(raw)}
        if system == "helpdesk":
            recs = sorted((r for r in crosswalk if r["source_table"] == "helpdesk_tickets" and r["raw_name"] == raw),
                          key=lambda r: r["source_key"])
            dates = sorted(tickets[r["source_key"]]["opened_at__value"][:10] for r in recs
                           if tickets[r["source_key"]]["opened_at__value"])
            item.update(email_domains="; ".join(email_domains(*[tickets[r["source_key"]]["description"] + " " +
                                                                 tickets[r["source_key"]]["resolution_notes"] for r in recs])),
                        first_date=dates[0] if dates else "", last_date=dates[-1] if dates else "")
            rows.append(item)
            if q["reason"] == "too close to another candidate":       # shared spelling: decide each record
                for r in recs:
                    t = tickets[r["source_key"]]
                    day = t["opened_at__value"][:10]
                    rows.append({**blank("helpdesk", t["ticket_id"]), "level": "record", "queue_item": unit,
                                 "records": "1", "name_as_written": raw, "queue_reason": q["reason"],
                                 **{k: v for k, v in item.items() if k.startswith(("candidate_", "score_"))},
                                 "email_domains": "; ".join(email_domains(t["description"], t["resolution_notes"])),
                                 "first_date": day, "last_date": day,
                                 "priority_category": f"{t['priority__value'] or t['priority']} / "
                                                      f"{t['category__value'] or t['category']}"})
        elif system == "shop":
            cid = unit.split()[-1]
            mine = [o for o in orders if o["customer_id"] == cid]
            dates = sorted(o["order_date__value"][:10] for o in mine if o["order_date__value"])
            total = sum(float(o["order_total__value"]) for o in mine
                        if o["revenue_treatment"] == "include" and o["order_total__value"])
            item.update(first_date=dates[0] if dates else "", last_date=dates[-1] if dates else "",
                        orders_total=f"{total:.2f}",
                        names_as_written="; ".join(sorted({o["company_name"] for o in mine})))
            rows.append(item)
        else:
            rows.append(item)
    return rows


def run(settings: dict) -> dict:
    rows = build_sheet(settings["validated_dir"], settings["resolved_dir"], settings["warehouse_dir"] / "duplicates.csv")
    path = settings["warehouse_dir"] / "review_sheet.csv"
    write_staging(rows, SHEET_COLUMNS, path)
    counts = defaultdict(int)
    for r in rows:
        counts[r["level"]] += 1
    return {"path": str(path), **counts}


def main() -> None:
    out = run(load_settings())
    print(f"Wrote {out['path']}: {out.get('item', 0)} queue items, {out.get('record', 0)} individual records")
    print("Fill in decided_account_id, reviewer, decided_on and reason for the rows you decide, copy those rows "
          "into config/review_decisions.csv, then run: python -m pipeline.warehouse.apply_review")


if __name__ == "__main__":
    main()
