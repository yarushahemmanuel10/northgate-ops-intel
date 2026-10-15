"""Rule-based duplicate marking for shop orders and telecoms bills (QI-04, D-024).

Run:  python -m pipeline.warehouse.dedup   (after pipeline.validate.run)

Reads data/validated/ only. Nothing is deleted: duplicates are listed in
data/warehouse/duplicates.csv with the record they repeat, and the warehouse load (step 3.3)
leaves them out of the facts.
"""
from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

import yaml

from pipeline.ingest.common import write_staging
from pipeline.settings import ROOT, load_settings

DUPLICATE_COLUMNS = ["table", "record_key", "source_row", "duplicate_of", "duplicate_of_row", "rule", "detail"]


def load_config(path: Path = ROOT / "config" / "warehouse.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def bill_duplicates(bills: list[dict], compare: list[str]) -> list[dict]:
    """Exact whole-row repeats; the first row in file order is kept."""
    first: dict[tuple, dict] = {}
    out = []
    for b in sorted(bills, key=lambda r: int(r["_source_row"])):
        key = tuple(b[c] for c in compare)
        if key in first:
            k = first[key]
            out.append({"table": "telecoms_bills", "record_key": b["bill_id"], "source_row": b["_source_row"],
                        "duplicate_of": k["bill_id"], "duplicate_of_row": k["_source_row"], "rule": "DUP-BILL",
                        "detail": "exact repeat of an earlier bill row (export re-run)"})
        else:
            first[key] = b
    return out


def _order_content(order: dict, lines: list[dict]) -> tuple:
    items = tuple(sorted((ln["sku"], ln["quantity__value"], ln["unit_price__value"]) for ln in lines))
    return (order["customer_id"], items, order["order_total__value"], order["status"])


def order_duplicates(orders: list[dict], lines: list[dict], window_minutes: float) -> list[dict]:
    """Double submissions: same content, placed within the window of the kept (earliest) order."""
    by_order = defaultdict(list)
    for ln in lines:
        by_order[ln["order_id"]].append(ln)
    groups: dict[tuple, list[tuple[dt.datetime, dict]]] = defaultdict(list)
    for o in orders:
        if not o["order_date__value"]:
            continue                                          # no valid time: never marked
        groups[_order_content(o, by_order[o["order_id"]])].append((dt.datetime.fromisoformat(o["order_date__value"]), o))
    out = []
    window = dt.timedelta(minutes=window_minutes)
    for members in groups.values():
        members.sort(key=lambda t: (t[0], t[1]["order_id"]))
        kept_time, kept = members[0]
        for when, o in members[1:]:
            if when - kept_time <= window:
                minutes = (when - kept_time).total_seconds() / 60
                out.append({"table": "shop_orders", "record_key": o["order_id"], "source_row": o["_source_row"],
                            "duplicate_of": kept["order_id"], "duplicate_of_row": kept["_source_row"],
                            "rule": "DUP-ORDER",
                            "detail": f"same customer, lines, total and status, placed {minutes:g} min after"})
            else:
                kept_time, kept = when, o                     # a genuine repeat starts a new original
    return out


def find_duplicates(validated: Path, cfg: dict) -> list[dict]:
    d = cfg["duplicates"]
    dups = bill_duplicates(_read(validated / "telecoms_bills.csv"), d["bills"]["compare"])
    dups += order_duplicates(_read(validated / "shop_orders.csv"), _read(validated / "shop_order_lines.csv"),
                             d["orders"]["window_minutes"])
    return sorted(dups, key=lambda r: (r["table"], int(r["source_row"]) if r["source_row"].isdigit() else 0,
                                       r["record_key"]))


def run(settings: dict, cfg: dict, validated: Path | None = None, out_dir: Path | None = None) -> dict:
    dups = find_duplicates(validated or settings["validated_dir"], cfg)
    write_staging(dups, DUPLICATE_COLUMNS, (out_dir or settings["warehouse_dir"]) / "duplicates.csv")
    counts: dict[str, int] = defaultdict(int)
    for r in dups:
        counts[r["table"]] += 1
    return dict(counts)


def main() -> None:
    print(run(load_settings(), load_config()))


if __name__ == "__main__":
    main()
