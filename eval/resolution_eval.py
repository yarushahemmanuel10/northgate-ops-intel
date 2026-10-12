"""Evaluate entity resolution against the answer key, on a development / held-out split.

  python -m eval.resolution_eval tune      # dev half only: score each candidate threshold
  python -m eval.resolution_eval report    # held-out half with the configured threshold,
                                           # named cases, review queue, BQ-06 payoff

The split is by true customer: each ACC- ID goes to the dev or held-out half by the parity
of an MD5 hash of the ID, so it is fixed and needs no random seed. Evaluation code may read
data/ground_truth/; pipeline code may not (D-017).
"""
from __future__ import annotations

import copy
import csv
import hashlib
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [70, 75, 80, 85, 88, 90, 92, 94, 96, 98]


def half(acc: str) -> str:
    return "dev" if int(hashlib.md5(acc.encode()).hexdigest(), 16) % 2 == 0 else "held-out"


def _read(path: Path, skip_marker: bool = False) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        if skip_marker:
            f.readline()
        return list(csv.DictReader(f))


def truth_maps(truth_dir: Path) -> dict:
    t = {r["ticket_id"]: r["account_id"] for r in _read(truth_dir / "tickets.csv", True)}
    shop = {r["shop_customer_id"]: r["account_id"] for r in _read(truth_dir / "shop_customers.csv", True)}
    tel = {r["account_no"]: r["account_id"] for r in _read(truth_dir / "telecoms_accounts.csv", True)}
    crm = {r["raw_account_id"]: r["account_id"] for r in _read(truth_dir / "crm_account_map.csv", True)}
    return {"tickets": t, "shop": shop, "telecoms": tel, "crm": crm}


def record_truth(row: dict, maps: dict, orders: dict, contracts: dict, bills: dict) -> str:
    s, k = row["source_table"], row["source_key"]
    if s == "crm_accounts":
        return maps["crm"][k]
    if s == "helpdesk_tickets":
        return maps["tickets"][k]
    if s == "shop_orders":
        return maps["shop"][orders[k]]
    if s == "telecoms_contracts":
        return maps["telecoms"][contracts[k]]
    if s == "telecoms_bills":
        return maps["telecoms"][bills[row["source_row"]]]
    raise KeyError(s)


def load_crosswalk(resolved: Path, validated: Path, truth_dir: Path) -> list[dict]:
    maps = truth_maps(truth_dir)
    orders = {o["order_id"]: o["customer_id"] for o in _read(validated / "shop_orders.csv")}
    contracts = {c["contract_ref"]: c["account_no"] for c in _read(validated / "telecoms_contracts.csv")}
    bills = {b["_source_row"]: b["account_no"] for b in _read(validated / "telecoms_bills.csv")}
    rows = _read(resolved / "crosswalk.csv")
    for r in rows:
        r["truth"] = record_truth(r, maps, orders, contracts, bills)
        r["unit"] = {"helpdesk_tickets": r["raw_name"], "shop_orders": f"shop {orders.get(r['source_key'], '')}",
                     "telecoms_contracts": contracts.get(r["source_key"], ""),
                     "telecoms_bills": bills.get(r["source_row"], ""), "crm_accounts": r["source_key"]}[r["source_table"]]
        r["unit_system"] = {"telecoms_bills": "telecoms_contracts"}.get(r["source_table"], r["source_table"])
    return rows


def metrics(rows: list[dict], which: str) -> dict:
    """Record- and name-level precision/recall of automatic matches, for one half."""
    recs = [r for r in rows if half(r["truth"]) == which and r["source_table"] != "crm_accounts"
            or (r["source_table"] == "crm_accounts" and r["source_key"] != r["truth"] and half(r["truth"]) == which)]
    auto = [r for r in recs if r["status"] == "auto"]
    correct = sum(r["resolved_account_id"] == r["truth"] for r in auto)
    out = {"records": len(recs), "auto": len(auto), "auto_correct": correct,
           "review": sum(r["status"] == "review" for r in recs),
           "unmatched": sum(r["status"] == "unmatched" for r in recs)}
    out["record_precision"] = correct / len(auto) if auto else None
    out["record_recall"] = correct / len(recs) if recs else None
    # names: each distinct (system, spelling or account) once
    units: dict[tuple, list[dict]] = {}
    for r in recs:
        if r["source_table"] == "telecoms_bills":
            continue                                     # the telecoms unit is the account (contract row)
        units.setdefault((r["unit_system"], r["unit"]), []).append(r)
    n_auto = n_correct = n_review = ambiguous = 0
    for (_, _), rs in units.items():
        truths = {r["truth"] for r in rs}
        ambiguous += len(truths) > 1
        st, acc = rs[0]["status"], rs[0]["resolved_account_id"]
        n_review += st == "review"
        if st == "auto":
            n_auto += 1
            n_correct += len(truths) == 1 and acc in truths
    out.update(names=len(units), names_auto=n_auto, names_auto_correct=n_correct, names_review=n_review,
               names_ambiguous=ambiguous,
               name_precision=n_correct / n_auto if n_auto else None,
               name_recall=n_correct / len(units) if units else None)
    return out


def run_resolver(threshold: float, validated: Path, out: Path) -> None:
    from pipeline.resolve.run import load_config, resolve
    from pipeline.settings import load_settings
    cfg = copy.deepcopy(load_config())
    cfg["decision"]["auto_threshold"] = threshold
    resolve(load_settings(), cfg, validated, out)


def tune(validated: Path, truth: Path) -> list[dict]:
    rows = []
    for t in CANDIDATES:
        with tempfile.TemporaryDirectory() as d:
            run_resolver(t, validated, Path(d))
            m = metrics(load_crosswalk(Path(d), validated, truth), "dev")
        rows.append({"threshold": t, **m})
    return rows


def choose(rows: list[dict], min_precision: float = 0.99) -> dict:
    ok = [r for r in rows if r["record_precision"] is not None and r["record_precision"] >= min_precision]
    return max(ok, key=lambda r: (r["record_recall"], -r["threshold"])) if ok else max(rows, key=lambda r: r["record_precision"] or 0)


def named_cases(rows: list[dict]) -> list[dict]:
    def recs(accs):
        return [r for r in rows if r["truth"] in accs]
    out = []
    la1 = recs({"ACC-0098", "ACC-0109"})
    wrong = [r for r in la1 if r["status"] == "auto" and r["resolved_account_id"] in {"ACC-0098", "ACC-0109"}
             and r["resolved_account_id"] != r["truth"]]
    out.append({"case": "LA-1 not auto-merged (ACC-0098 / ACC-0109)", "pass": not wrong,
                "halves": f"{half('ACC-0098')} / {half('ACC-0109')}",
                "detail": f"{len(la1)} records: {sum(r['status'] == 'auto' and r['resolved_account_id'] == r['truth'] for r in la1)} "
                          f"correct auto, {sum(r['status'] == 'review' for r in la1)} queued, {len(wrong)} merged into the other"})
    la4 = recs({"ACC-0028", "ACC-0106"})
    wrong4 = [r for r in la4 if r["status"] == "auto" and r["resolved_account_id"] != r["truth"]]
    out.append({"case": "LA-4 matched correctly or queued (ACC-0028 / ACC-0106)", "pass": not wrong4,
                "halves": f"{half('ACC-0028')} / {half('ACC-0106')}",
                "detail": f"{len(la4)} records: {sum(r['status'] == 'auto' and r['resolved_account_id'] == r['truth'] for r in la4)} "
                          f"correct auto, {sum(r['status'] == 'review' for r in la4)} queued, {len(wrong4)} wrong"})
    dup = next(r for r in rows if r["source_table"] == "crm_accounts" and r["source_key"] == "ACC-0123")
    out.append({"case": "CRM duplicate ACC-0123 merged into ACC-0027", "pass": dup["status"] == "auto" and dup["resolved_account_id"] == "ACC-0027",
                "halves": half("ACC-0027"), "detail": f"{dup['status']} -> {dup['resolved_account_id'] or '(none)'}"})
    sw = [r for r in rows if r["truth"] == "ACC-0047" and r["source_table"] == "shop_orders"]
    accounts = {r["unit"]: (r["status"], r["resolved_account_id"]) for r in sw}
    out.append({"case": "Switch trap: both ACC-0047 shop accounts resolve to ACC-0047",
                "pass": len(accounts) == 2 and all(v == ("auto", "ACC-0047") for v in accounts.values()),
                "halves": half("ACC-0047"), "detail": str(accounts)})
    return out


def payoff(validated: Path, resolved: Path, review_resolved_by: dict | None = None) -> dict:
    """BQ-06 pattern on the pipeline's own crosswalk (queued records treated as unmatched).

    review_resolved_by: optional {(source_table, source_key, source_row): account} to simulate a
    person resolving the review queue correctly."""
    import pandas as pd
    from generator.config import load_config as gen_config
    from generator.patterns import _quarters, flags
    cfg = gen_config()
    cw = pd.DataFrame(_read(resolved / "crosswalk.csv"))
    if review_resolved_by:
        for i, r in cw[cw.status == "review"].iterrows():
            cw.at[i, "resolved_account_id"] = review_resolved_by[(r.source_table, r.source_key, r.source_row)]
            cw.at[i, "status"] = "auto"
    cw = cw[cw.status == "auto"]
    t = pd.DataFrame(_read(validated / "helpdesk_tickets.csv"))
    t = t.merge(cw[cw.source_table == "helpdesk_tickets"][["source_key", "resolved_account_id"]],
                left_on="ticket_id", right_on="source_key")
    t["month"], t["acc"] = t["opened_at__value"].str[:7], t["resolved_account_id"]
    b = pd.DataFrame(_read(validated / "telecoms_bills.csv"))
    original_cols = ["bill_id", "account_no", "bill_month", "bill_date", "line_rental", "call_charges", "total"]
    b = b.drop_duplicates(subset=original_cols)                       # re-run export rows are identical
    b = b.merge(cw[cw.source_table == "telecoms_bills"][["source_row", "resolved_account_id"]],
                left_on="_source_row", right_on="source_row")
    b["month"], b["acc"], b["total_v"] = b["bill_month__value"].str[:7], b["resolved_account_id"], b["total__value"].astype(float)
    o = pd.DataFrame(_read(validated / "shop_orders.csv"))
    o = o[o.revenue_treatment == "include"]
    o = o.merge(cw[cw.source_table == "shop_orders"][["source_key", "resolved_account_id"]],
                left_on="order_id", right_on="source_key")
    o["month"], o["acc"], o["total_v"] = o["order_date__value"].str[:7], o["resolved_account_id"], o["order_total__value"].astype(float)
    m = {"tickets": _quarters(t, "acc", None), "bl2": _quarters(b, "acc", "total_v"), "shop": _quarters(o, "acc", "total_v")}
    crm = pd.DataFrame(_read(validated / "crm_accounts.csv"))
    active = set(crm.loc[crm.Status == "active", "Account ID"])
    f = flags(cfg, m, active)
    return {"flagged": sorted(f.index[f.pattern & f.active])}


def main() -> None:
    validated, truth, resolved = ROOT / "data" / "validated", ROOT / "data" / "ground_truth", ROOT / "data" / "resolved"
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"
    if mode == "tune":
        rows = tune(validated, truth)
        print("Dev half only - record level (auto precision / recall) and name level:")
        print(f"{'threshold':>9} {'records':>7} {'auto':>5} {'prec':>7} {'recall':>7} {'review':>6} {'names':>5} {'n_prec':>7} {'n_rec':>7} {'n_rev':>5}")
        for r in rows:
            print(f"{r['threshold']:>9} {r['records']:>7} {r['auto']:>5} {r['record_precision']:>7.2%} {r['record_recall']:>7.2%} "
                  f"{r['review']:>6} {r['names']:>5} {r['name_precision']:>7.2%} {r['name_recall']:>7.2%} {r['names_review']:>5}")
        c = choose(rows)
        print(f"Chosen (record precision >= 99% on dev, then highest recall): {c['threshold']}")
        return
    rows = load_crosswalk(resolved, validated, truth)
    for which in ("held-out", "dev"):
        m = metrics(rows, which)
        print(f"{which}: records {m['records']} (auto {m['auto']}, review {m['review']}, unmatched {m['unmatched']}); "
              f"record precision {m['record_precision']:.2%}, recall {m['record_recall']:.2%}; "
              f"names {m['names']} (auto {m['names_auto']}, review {m['names_review']}, ambiguous {m['names_ambiguous']}); "
              f"name precision {m['name_precision']:.2%}, recall {m['name_recall']:.2%}")
    by_sys = Counter((r["source_table"], r["status"]) for r in rows if half(r["truth"]) == "held-out")
    print("held-out by table and status:", dict(sorted(by_sys.items())))
    wrong = [r for r in rows if half(r["truth"]) == "held-out" and r["status"] == "auto" and r["resolved_account_id"] != r["truth"]]
    print(f"held-out wrong auto matches: {len(wrong)}; distinct names: {sorted({(r['source_table'], r['raw_name'], r['resolved_account_id'], r['truth']) for r in wrong})[:8]}")
    queue = _read(resolved / "review_queue.csv")
    print(f"review queue: {len(queue)} names/accounts covering {sum(int(q['records']) for q in queue)} records "
          f"({Counter(q['source_system'] for q in queue)})")
    print("named cases:")
    for c in named_cases(rows):
        print(f"  {'PASS' if c['pass'] else 'FAIL'}  {c['case']} [{c['halves']}]: {c['detail']}")
    p = payoff(validated, resolved)
    risk = {"ACC-0004", "ACC-0007", "ACC-0020", "ACC-0027", "ACC-0050", "ACC-0064", "ACC-0082", "ACC-0106", "ACC-0109", "ACC-0115"}
    print(f"BQ-06 on the pipeline's crosswalk (queued = unmatched): {len(set(p['flagged']) & risk)} of 10 at-risk found; "
          f"healthy flagged {sorted(set(p['flagged']) - risk)}; missed {sorted(risk - set(p['flagged']))}")
    fix = {(r["source_table"], r["source_key"], r["source_row"]): r["truth"] for r in rows if r["status"] == "review"}
    p2 = payoff(validated, resolved, fix)
    print(f"BQ-06 after a person resolves the review queue correctly: {len(set(p2['flagged']) & risk)} of 10 at-risk found; "
          f"healthy flagged {sorted(set(p2['flagged']) - risk)}; missed {sorted(risk - set(p2['flagged']))}")


if __name__ == "__main__":
    main()
