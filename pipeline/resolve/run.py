"""Resolve every source record to a canonical CRM account.

Run:  python -m pipeline.resolve.run   (after pipeline.validate.run)

Reads data/validated/; writes data/resolved/crosswalk.csv (one row per source record) and
data/resolved/review_queue.csv (one row per distinct name or account needing a person).

Evidence used, all from data/validated/ (never the answer key, D-017):
- the name (normalised; billing-system truncation; common abbreviations; fuzzy score)
- the legal form written in the name (Ltd / LLP / CIO), to tell apart names that differ only
  in legal form
- for telecoms accounts, the contract start date compared with the start dates of CRM BL2
  contract lines
- for CRM duplicates, whether each record has contract lines
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import yaml

from pipeline.ingest.common import write_staging
from pipeline.resolve.names import abbreviation_keys, billing_keys, legal_form, norm, similarity
from pipeline.settings import ROOT, load_settings

CROSSWALK_COLUMNS = ["source_system", "source_table", "source_key", "source_row", "raw_name",
                     "resolved_account_id", "method", "score", "status", "detail"]
QUEUE_COLUMNS = ["source_system", "unit", "raw_name", "records", "candidate_1", "score_1", "candidate_2",
                 "score_2", "reason"]


def load_config(path: Path = ROOT / "config" / "resolution.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class Resolver:
    def __init__(self, cfg: dict, accounts: list[dict], lines: list[dict]):
        self.cfg = cfg
        self.suffixes = cfg["legal_suffixes"]
        d = cfg["decision"]
        if d["auto_threshold"] is None:
            raise ValueError("auto_threshold is not set in config/resolution.yaml (chosen on the dev half, D-019)")
        self.auto, self.review, self.margin = d["auto_threshold"], d["review_threshold"], d["margin"]
        self.lines_by_acc: dict[str, list[dict]] = {}
        for ln in lines:
            self.lines_by_acc.setdefault(ln["Account ID"], []).append(ln)
        self.accounts = {a["Account ID"]: a for a in accounts}
        self.main_of = self._crm_duplicates(accounts)
        self.canonical = [a for a in accounts if self.main_of[a["Account ID"]] == a["Account ID"]]
        self._index()

    # ---- CRM duplicates ---------------------------------------------------------------
    def _crm_duplicates(self, accounts):
        main_of = {a["Account ID"]: a["Account ID"] for a in accounts}
        self.crm_findings = {}
        normed = {a["Account ID"]: norm(a["Company Name"], self.suffixes) for a in accounts}
        ids = sorted(normed)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                score = similarity(normed[a], normed[b])
                if score < self.review:
                    continue
                with_lines = [x for x in (a, b) if self.lines_by_acc.get(x)]
                fa, fb = legal_form(self.accounts[a]["Company Name"]), legal_form(self.accounts[b]["Company Name"])
                differ_in_form = fa and fb and fa != fb
                if score >= self.auto and len(with_lines) <= 1 and not differ_in_form:
                    main = self._main(a, b)
                    other = b if main == a else a
                    main_of[other] = main_of.get(main, main)
                    self.crm_findings[other] = ("auto", score, main, "name match; at most one record has contract lines")
                else:
                    reason = ("both have contract lines" if len(with_lines) == 2 else
                              "legal forms differ" if differ_in_form else "score below auto threshold")
                    for x, y in ((a, b), (b, a)):
                        self.crm_findings.setdefault(x, ("review", score, y, f"possible duplicate of {y}: {reason}"))
        return main_of

    def _main(self, a, b):
        def key(x):
            acc = self.accounts[x]
            since = acc.get("Customer Since__value") or "9999-99-99"
            return (0 if self.lines_by_acc.get(x) else 1, since, x)
        return min((a, b), key=key)

    # ---- candidates ---------------------------------------------------------------------
    def _index(self):
        self.norms = {a["Account ID"]: norm(a["Company Name"], self.suffixes) for a in self.canonical}
        self.billing = {acc: billing_keys(self.accounts[acc]["Company Name"], self.cfg["billing_field_length"])
                        for acc in self.norms}
        self.abbrev = {acc: abbreviation_keys(n) for acc, n in self.norms.items()}

    def score_name(self, raw: str) -> list[tuple[str, float, str]]:
        ns = norm(raw, self.suffixes)
        upper = raw.strip().upper()
        out = []
        for acc, n in self.norms.items():
            if ns == n:
                out.append((acc, 100.0, "exact"))
            elif upper == raw.strip() and upper in self.billing[acc]:
                out.append((acc, 100.0, "exact"))           # billing-system style: upper case, truncated
            elif ns in self.abbrev[acc]:
                out.append((acc, float(self.cfg["scores"]["abbreviation"]), "fuzzy"))
            else:
                out.append((acc, float(similarity(ns, n)), "fuzzy"))
        out.sort(key=lambda t: (-t[1], t[0]))
        return out

    def decide(self, raw: str, extra_evidence=None) -> dict:
        cands = self.score_name(raw)
        top = cands[0]
        tied = [c for c in cands if top[1] - c[1] < self.margin]
        if top[1] >= self.auto and len(tied) == 1:
            return {"acc": top[0], "method": top[2], "score": top[1], "status": "auto", "detail": "", "cands": cands}
        if top[1] >= self.auto and len(tied) > 1:
            # names too close to call: use the legal form written in the source name
            form = legal_form(raw)
            if form:
                same = [c for c in tied if legal_form(self.accounts[c[0]]["Company Name"]) == form]
                if len(same) == 1:
                    return {"acc": same[0][0], "method": "evidence", "score": same[0][1], "status": "auto",
                            "detail": f"legal form '{form}' matches only this candidate", "cands": cands}
            if extra_evidence:
                pick = extra_evidence([c[0] for c in tied])
                if pick:
                    return {"acc": pick[0], "method": "evidence", "score": top[1], "status": "auto",
                            "detail": pick[1], "cands": cands}
        if top[1] >= self.review:
            reason = "too close to another candidate" if len(tied) > 1 else "score below auto threshold"
            return {"acc": "", "method": top[2], "score": top[1], "status": "review", "detail": reason, "cands": cands}
        return {"acc": "", "method": "unmatched", "score": top[1], "status": "unmatched", "detail": "", "cands": cands}

    def telecoms_evidence(self, start_date: str):
        def pick(candidates):
            hits = [acc for acc in candidates
                    if any(ln["Business Line"] == "BL2" and ln.get("Contract Start__value") == start_date
                           for ln in self.lines_by_acc.get(acc, []))]
            if len(hits) == 1:
                return hits[0], f"contract start {start_date} matches this customer's CRM BL2 line"
            return None
        return pick


def resolve(settings: dict, cfg: dict, validated_dir: Path | None = None, out_dir: Path | None = None) -> dict:
    vd = validated_dir or settings["validated_dir"]
    out = out_dir or settings["resolved_dir"]
    accounts = _read(vd / "crm_accounts.csv")
    lines = _read(vd / "crm_contract_lines.csv")
    r = Resolver(cfg, accounts, lines)
    crosswalk, queue = [], []

    def row(system, table, key, src_row, raw, d):
        return {"source_system": system, "source_table": table, "source_key": key, "source_row": src_row,
                "raw_name": raw, "resolved_account_id": d["acc"], "method": d["method"],
                "score": f"{d['score']:.1f}", "status": d["status"], "detail": d["detail"]}

    def queue_row(system, unit, raw, n, d, reason):
        c = d.get("cands", [])
        queue.append({"source_system": system, "unit": unit, "raw_name": raw, "records": n,
                      "candidate_1": c[0][0] if c else "", "score_1": f"{c[0][1]:.1f}" if c else "",
                      "candidate_2": c[1][0] if len(c) > 1 else "", "score_2": f"{c[1][1]:.1f}" if len(c) > 1 else "",
                      "reason": reason})

    # CRM accounts: canonical rows map to themselves; duplicates to their main record
    for a in accounts:
        acc = a["Account ID"]
        f = r.crm_findings.get(acc)
        if r.main_of[acc] != acc:
            d = {"acc": r.main_of[acc], "method": "exact" if f[1] >= 100 else "fuzzy", "score": f[1],
                 "status": "auto", "detail": f"duplicate of {r.main_of[acc]}: {f[3]}"}
        elif f and f[0] == "review":
            d = {"acc": acc, "method": "exact", "score": 100.0, "status": "auto", "detail": f[3]}
            queue_row("crm", acc, a["Company Name"], 1, {"cands": [(f[2], f[1])]}, f[3])
        else:
            d = {"acc": acc, "method": "exact", "score": 100.0, "status": "auto", "detail": "CRM account"}
        crosswalk.append(row("crm", "crm_accounts", acc, a["_source_row"], a["Company Name"], d))

    # helpdesk: one decision per distinct name
    tickets = _read(vd / "helpdesk_tickets.csv")
    name_counts = Counter(t["customer_name"] for t in tickets)
    decisions = {n: r.decide(n) for n in sorted(name_counts)}
    for n, d in decisions.items():
        if d["status"] == "review":
            queue_row("helpdesk", n, n, name_counts[n], d, d["detail"])
    for t in tickets:
        crosswalk.append(row("helpdesk", "helpdesk_tickets", t["ticket_id"], t["_source_row"], t["customer_name"],
                             decisions[t["customer_name"]]))

    # shop: one decision per shop account (name set at sign-up)
    orders = _read(vd / "shop_orders.csv")
    by_cust: dict[str, Counter] = {}
    for o in orders:
        by_cust.setdefault(o["customer_id"], Counter())[o["company_name"]] += 1
    shop_dec = {}
    for cid in sorted(by_cust, key=int):
        name = by_cust[cid].most_common(1)[0][0]
        shop_dec[cid] = r.decide(name)
        if shop_dec[cid]["status"] == "review":
            queue_row("shop", f"shop customer {cid}", name, sum(by_cust[cid].values()), shop_dec[cid],
                      shop_dec[cid]["detail"])
    for o in orders:
        crosswalk.append(row("shop", "shop_orders", o["order_id"], o["_source_row"], o["company_name"],
                             shop_dec[o["customer_id"]]))

    # telecoms: one decision per account, with contract-date evidence for close calls
    contracts = _read(vd / "telecoms_contracts.csv")
    tel_dec = {}
    for c in contracts:
        d = r.decide(c["account_name"], r.telecoms_evidence(c["start_date__value"]))
        tel_dec[c["account_no"]] = d
        crosswalk.append(row("telecoms", "telecoms_contracts", c["contract_ref"], c["_source_row"],
                             c["account_name"], d))
    bills = _read(vd / "telecoms_bills.csv")
    bill_counts = Counter(b["account_no"] for b in bills)
    for c in contracts:
        d = tel_dec[c["account_no"]]
        if d["status"] == "review":
            queue_row("telecoms", c["account_no"], c["account_name"], bill_counts[c["account_no"]] + 1, d, d["detail"])
    for b in bills:
        d = tel_dec.get(b["account_no"], {"acc": "", "method": "unmatched", "score": 0.0, "status": "unmatched",
                                          "detail": "no telecoms contract for this account"})
        name = next((c["account_name"] for c in contracts if c["account_no"] == b["account_no"]), "")
        crosswalk.append(row("telecoms", "telecoms_bills", b["bill_id"], b["_source_row"], name, d))

    write_staging(crosswalk, CROSSWALK_COLUMNS, out / "crosswalk.csv")
    write_staging(queue, QUEUE_COLUMNS, out / "review_queue.csv")
    return {"crosswalk": len(crosswalk), "review_queue": len(queue),
            "statuses": dict(Counter(c["status"] for c in crosswalk))}


def main() -> None:
    settings = load_settings()
    print(resolve(settings, load_config()))


if __name__ == "__main__":
    main()
