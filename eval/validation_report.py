"""Score Layer 1 validation against the answer key and the unseen-values fixture.

Run:  python -m eval.validation_report   (after pipeline.validate.run)

This is evaluation code, not pipeline code: it may read data/ground_truth/. The answer-key
scores are optimistic, because the same work designed both the mess and the rules (D-018);
the unseen-values fixture is the more honest check of how the rules generalise.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE = {"crm_accounts": "crm_accounts", "crm_lines": "crm_contract_lines", "helpdesk": "helpdesk_tickets",
         "telecoms_bills": "telecoms_bills", "telecoms_contracts": "telecoms_contracts", "shop_orders": "shop_orders"}
FIELD = {"customer_since": "Customer Since", "closed_date": "Closed Date", "contract_start": "Contract Start",
         "renewal_date": "Renewal Date", "end_date": "End Date", "annual_value": "Annual Value (GBP)"}


def _read(path: Path, skip_marker: bool = False) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        if skip_marker:
            f.readline()
        return list(csv.DictReader(f))


def _decode(s: str):
    w = json.loads(s)
    for k in ("d", "dt", "v"):
        if k in w:
            return w[k]
    return w


def _key(table, rec, field):
    return (TABLE.get(table, table), rec, FIELD.get(field, field))


def score(known_keys: set, found_keys: set, examples_from: dict | None = None) -> dict:
    tp, fp, fn = known_keys & found_keys, found_keys - known_keys, known_keys - found_keys
    out = {"known": len(known_keys), "found": len(found_keys), "tp": len(tp), "fp": len(fp), "fn": len(fn),
           "recall": len(tp) / len(known_keys) if known_keys else None,
           "precision": len(tp) / len(found_keys) if found_keys else None}
    out["fp_examples"] = [examples_from.get(k, k) for k in sorted(fp)[:3]] if examples_from else sorted(fp)[:3]
    out["fn_examples"] = sorted(fn)[:3]
    out["fp_keys"] = sorted(fp)
    return out


def answer_key_scores(validated: Path, truth: Path) -> dict:
    known = _read(truth / "known_issues.csv", skip_marker=True)
    findings = _read(validated / "validation_findings.csv")
    by_rule: dict[str, dict] = {}
    for f in findings:
        by_rule.setdefault(f["rule_id"], {})[(f["table"], f["record_key"], f["field"])] = f

    # An injected change only counts if it survives in the raw file: a later entry may have
    # overwritten the same field (e.g. QI-08 removing a date QI-03 had reformatted) or removed
    # the row (QI-11). Those are reported separately, not as misses.
    last_touch, removed = {}, set()
    for i, e in enumerate(known):
        if e["op"] == "change":
            last_touch[(e["table"], e["record_key"], e["field"])] = i
        elif e["op"] == "remove_row":
            removed.add((e["table"], e["record_key"]))
    superseded = {}

    def kset(qi, fields=None, op="change", table=None):
        out = {}
        for i, e in enumerate(known):
            if not (e["qi"] == qi and e["op"] == op and (fields is None or e["field"] in fields)
                    and (table is None or e["table"] == table)):
                continue
            if last_touch.get((e["table"], e["record_key"], e["field"])) != i or (e["table"], e["record_key"]) in removed:
                superseded[qi] = superseded.get(qi, 0) + 1
                continue
            out[_key(e["table"], e["record_key"], e["field"])] = e
        return out

    def fset(*rules, field=None):
        out = {}
        for r in rules:
            for k, f in by_rule.get(r, {}).items():
                if field is None or k[2] == field:
                    out[k] = f
        return out

    res = {}
    # QI-03 dates: detection and exact correctness
    k3 = kset("QI-03")
    f3 = fset("DATE-001", "DATE-002")
    s = score(set(k3), set(f3), {k: f"{k} {f3[k]['original']!r} ({f3[k]['detail']})" for k in f3})
    correct = 0
    for k in set(k3) & set(f3):
        orig, val = str(_decode(k3[k]["original_json"])), f3[k]["value"]
        correct += (val[:7] == orig[:7]) if k[0] == "telecoms_bills" else (val == orig)
    s["converted_exactly_right"] = correct
    s["by_convention"] = sum(1 for f in f3.values() if f["action"] == "converted_by_convention")
    s["superseded_by_later_problems"] = superseded.get("QI-03", 0)
    res["QI-03 dates"] = s
    # QI-05
    kv = kset("QI-05", {"annual_value"})
    fv = fset("MONEY-001", field="Annual Value (GBP)")
    s = score(set(kv), set(fv))
    s["converted_exactly_right"] = sum(1 for k in set(kv) & set(fv)
                                       if abs(float(fv[k]["value"]) - float(_decode(kv[k]["original_json"]))) < 0.005)
    res["QI-05 contract values"] = s
    kp = kset("QI-05", {"priority"})
    fp_ = fset("PRIORITY-002")
    s = score(set(kp), set(fp_))
    s["typed_value_empty"] = all(fp_[k]["value"] == "" for k in set(kp) & set(fp_))
    s["values"] = sorted(fp_[k]["original"] for k in fp_)
    res["QI-05 invalid priority"] = s
    res["QI-05 closed before opened"] = score(set(kset("QI-05", {"closed_at"})), set(fset("TIME-001")))
    kq = {(TABLE["shop_orders"], e["record_key"]) for e in known if e["qi"] == "QI-05" and e["field"] == "lines"}
    res["QI-05 quantity (QTY-001)"] = score(kq, {(k[0].replace("shop_order_lines", "shop_orders"), k[1])
                                                 for k in fset("QTY-001")})
    res["QI-05 quantity (ARITH-001 order total)"] = score(kq, {(k[0], k[1]) for k in fset("ARITH-001")})
    # QI-08
    res["QI-08 renewal dates"] = score(set(kset("QI-08", {"renewal_date"})), set(fset("MISSING-003")))
    res["QI-08 delivered dates"] = score(set(kset("QI-08", {"delivered_date"})), set(fset("MISSING-002")))
    k8 = kset("QI-08", {"first_responded_at"})
    s = score(set(k8), set(fset("MISSING-001")))
    s["fn_note"] = "missing response on a ticket still open at the as-of date is indistinguishable from not yet answered"
    res["QI-08 first response"] = s
    s = score(set(kset("QI-08", {"kb_article_id"})), set())
    s["fn_note"] = "by design: an unrecorded KB link looks exactly like a ticket that used no article"
    res["QI-08 KB links"] = s
    # QI-10 on the revenue label
    k10 = set()
    for e in known:
        if e["qi"] == "QI-10" and e["op"] == "add_row":
            row = json.loads(e["new_json"])["o"]
            key = row["order_id"]["v"] if e["table"] == "shop_orders" else row["bill_id"]["v"]
            k10.add((TABLE[e["table"]], key))
    labelled = {("shop_orders", r["order_id"]) for r in _read(validated / "shop_orders.csv") if r["revenue_treatment"] == "exclude"}
    labelled |= {("telecoms_bills", r["bill_id"]) for r in _read(validated / "telecoms_bills.csv") if r["revenue_treatment"] == "negative"}
    res["QI-10 revenue treatment label"] = score(k10, labelled)
    # tickets whose response SLA can be measured
    tickets = _read(validated / "helpdesk_tickets.csv")
    closed = [t for t in tickets if t["closed_at"].strip()]
    res["closed tickets"] = len(closed)
    res["response SLA measurable"] = sum(t["response_sla_measurable"] == "true" for t in closed)
    return res


def unseen_scores(path: Path = ROOT / "tests" / "fixtures" / "unseen_values.csv") -> dict:
    from pipeline.validate.parse import parse_date, parse_money, parse_priority
    from pipeline.validate.run import load_rules
    rules = load_rules()
    plausible = tuple(rules["dates"]["plausible_range"])
    rows = []
    for r in _read(path):
        if r["kind"] == "date":
            value, action, _ = parse_date(r["value"], rules["dates"]["crm"], plausible)
            value = value.isoformat() if value else None
        elif r["kind"] == "amount":
            value, action, _ = parse_money(r["value"], rules["money"]["allow_k_suffix"])
            value = f"{value:g}" if value is not None else None
        else:
            p = rules["priorities"]
            value, action, _ = parse_priority(r["value"], p["valid"], p["case_insensitive"], p["mapping"])
        truth = r["truth"]
        if value is None:
            outcome = "flagged correctly" if truth == "INVALID" else "flagged (not converted)"
        elif truth != "INVALID" and str(value) == truth:
            outcome = "converted correctly"
        else:
            outcome = "WRONGLY converted"
        rows.append({**r, "result": value, "outcome": outcome})
    summary = {}
    for row in rows:
        summary[row["outcome"]] = summary.get(row["outcome"], 0) + 1
    return {"rows": rows, "summary": summary}


def main() -> None:
    validated, truth = ROOT / "data" / "validated", ROOT / "data" / "ground_truth"
    res = answer_key_scores(validated, truth)
    print("Answer-key scores (optimistic: the same work designed the mess and the rules)")
    for name, s in res.items():
        if not isinstance(s, dict):
            print(f"  {name}: {s}")
            continue
        extra = {k: v for k, v in s.items() if k not in ("known", "found", "tp", "fp", "fn", "recall", "precision",
                                                          "fp_examples", "fn_examples", "fp_keys")}
        rec = f"{s['recall']:.1%}" if s["recall"] is not None else "-"
        prec = f"{s['precision']:.1%}" if s["precision"] is not None else "-"
        print(f"  {name}: known {s['known']}, TP {s['tp']}, FP {s['fp']}, FN {s['fn']}, recall {rec}, "
              f"precision {prec} {extra if extra else ''}")
        for ex in s["fp_examples"]:
            print(f"      FP example: {ex}")
        if s["fn"] and s["fn"] <= 5:
            print(f"      FN: {s['fn_examples']}")
    u = unseen_scores()
    print(f"\nUnseen values fixture ({len(u['rows'])} values, written before the rules): {u['summary']}")
    for r in u["rows"]:
        print(f"  {r['kind']:8s} {r['value']!r:22s} truth {r['truth']:11s} -> {str(r['result']):11s} {r['outcome']}")


if __name__ == "__main__":
    main()
