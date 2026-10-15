"""Score duplicate marking (QI-04, D-024) against the answer key.

Run:  python -m eval.dedup_eval   (after pipeline.validate.run)

Evaluation code: it may read data/ground_truth/; the pipeline may not (D-017).
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from pipeline.warehouse.dedup import bill_duplicates, load_config, order_duplicates

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = [5, 10, 30, 60]
BL5_CLEANED = 734394          # generator summary: BL5 revenue on correctly cleaned data (D-016)


def _read(path: Path, skip_marker: bool = False) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        if skip_marker:
            f.readline()
        return list(csv.DictReader(f))


def answer_key(truth: Path) -> dict:
    known = _read(truth / "known_issues.csv", skip_marker=True)
    orders = {e["record_key"]: e["note"].split()[-1] for e in known
              if e["qi"] == "QI-04" and e["table"] == "shop_orders" and e["op"] == "add_row"}
    bills = {e["record_key"] for e in known if e["qi"] == "QI-04" and e["table"] == "telecoms_bills" and e["op"] == "add_row"}
    qi10 = set()
    for e in known:
        if e["qi"] == "QI-10" and e["op"] == "add_row":
            row = json.loads(e["new_json"])["o"]
            qi10.add(row["order_id"]["v"] if e["table"] == "shop_orders" else row["bill_id"]["v"])
    return {"orders": orders, "bills": bills, "qi10": qi10}


def genuine_repeats(orders: list[dict], lines: list[dict], key: dict) -> set[str]:
    """Orders whose customer already placed an order with identical items (SKU, quantity, unit
    price), excluding injected duplicates and QI-10 rows: real repeat purchases."""
    items = defaultdict(list)
    for ln in lines:
        items[ln["order_id"]].append((ln["sku"], ln["quantity__value"], ln["unit_price__value"]))
    seen, out = set(), set()
    for o in sorted(orders, key=lambda r: (r["order_date__value"], r["order_id"])):
        if o["order_id"] in key["orders"] or o["order_id"] in key["qi10"]:
            continue
        k = (o["customer_id"], tuple(sorted(items[o["order_id"]])))
        if k in seen:
            out.add(o["order_id"])
        seen.add(k)
    return out


def score_orders(marked: list[dict], key: dict, repeats: set[str]) -> dict:
    found = {m["record_key"]: m["duplicate_of"] for m in marked}
    tp = [k for k in found if k in key["orders"]]
    fp = [k for k in found if k not in key["orders"]]
    return {"known": len(key["orders"]), "marked": len(found), "tp": len(tp), "fp": len(fp),
            "fn": len(set(key["orders"]) - set(found)),
            "precision": len(tp) / len(found) if found else None,
            "recall": len(tp) / len(key["orders"]) if key["orders"] else None,
            "kept_copy_right": sum(found[k] == key["orders"][k] for k in tp),
            "genuine_repeats_marked": len(set(found) & repeats),
            "qi10_marked": len(set(found) & key["qi10"]),
            "fp_examples": [f"{k} marked as duplicate of {found[k]}" for k in sorted(fp)[:3]]}


def bl5_revenue(orders: list[dict], exclude: set[str]) -> float:
    return sum(float(o["order_total__value"]) for o in orders
               if o["revenue_treatment"] == "include" and o["order_id"] not in exclude)


def main() -> None:
    validated, truth = ROOT / "data" / "validated", ROOT / "data" / "ground_truth"
    cfg = load_config()["duplicates"]
    key = answer_key(truth)
    orders, lines = _read(validated / "shop_orders.csv"), _read(validated / "shop_order_lines.csv")
    repeats = genuine_repeats(orders, lines, key)
    print(f"Genuine repeat purchases (same customer, identical items, not injected): {len(repeats)}")
    print("Orders, by window (chosen window: "
          f"{cfg['orders']['window_minutes']} min):")
    for w in WINDOWS:
        s = score_orders(order_duplicates(orders, lines, w), key, repeats)
        print(f"  {w:>3} min: marked {s['marked']}, TP {s['tp']}, FP {s['fp']}, FN {s['fn']}, "
              f"precision {s['precision']:.1%}, recall {s['recall']:.1%}, kept copy right {s['kept_copy_right']}/{s['tp']}, "
              f"genuine repeats marked {s['genuine_repeats_marked']}, QI-10 rows marked {s['qi10_marked']}")
        for ex in s["fp_examples"]:
            print(f"        FP example: {ex}")
    b = bill_duplicates(_read(validated / "telecoms_bills.csv"), cfg["bills"]["compare"])
    marked_bills = {m["record_key"] for m in b}
    tp = marked_bills & key["bills"]
    print(f"Bills: known {len(key['bills'])}, marked {len(b)}, TP {len(tp)}, FP {len(marked_bills - key['bills'])}, "
          f"FN {len(key['bills'] - marked_bills)}, QI-10 rows marked {len(marked_bills & key['qi10'])}; "
          f"FP examples {sorted(marked_bills - key['bills'])[:3]}")
    chosen = {m["record_key"] for m in order_duplicates(orders, lines, cfg["orders"]["window_minutes"])}
    before, after = bl5_revenue(orders, set()), bl5_revenue(orders, chosen)
    print(f"BL5 revenue (include rows): before dedup £{before:,.2f}; after £{after:,.2f}; "
          f"correctly cleaned £{BL5_CLEANED:,}; gap before £{before - BL5_CLEANED:,.2f}, after £{after - BL5_CLEANED:,.2f}; "
          f"share of gap closed {(before - after) / (before - BL5_CLEANED):.1%}" if before != BL5_CLEANED else "")


if __name__ == "__main__":
    main()
