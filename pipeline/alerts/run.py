"""Layer 4 alert runner (step 5.1, D-034).

Run:  python -m pipeline.alerts.run   (after pipeline.warehouse.load; also the last step of pipeline.run)

Evaluates config/alert_rules.yaml against the warehouse (opened read-only) and keeps the alert
history in data/alerts/alerts.duckdb, which survives warehouse rebuilds:
- alerts:    one row per rule and record; first raised, last seen, cleared, times raised
- run_log:   one row per run, with counts and status
- run_rules: alerts matched per rule per run
An alert still true on the next run keeps its first-raised time; one no longer true is marked
cleared (never deleted); one that comes back is reopened. A plain Python runner, not Prefect:
see D-034. Times are UTC. Nothing here calls an external service.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

import duckdb
import yaml

from pipeline.settings import ROOT, load_settings
from pipeline.warehouse.load import load_config as warehouse_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    alert_id VARCHAR PRIMARY KEY, rule_id VARCHAR, bq VARCHAR, severity VARCHAR, record_key VARCHAR,
    customer_id VARCHAR, company_name VARCHAR, reason VARCHAR, status VARCHAR,
    first_raised_at TIMESTAMP, last_seen_at TIMESTAMP, cleared_at TIMESTAMP, times_raised INTEGER,
    data_as_of DATE);
CREATE TABLE IF NOT EXISTS run_log (
    run_id INTEGER PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP, data_as_of DATE,
    status VARCHAR, rules_evaluated INTEGER, alerts_active INTEGER, alerts_new INTEGER,
    alerts_cleared INTEGER, error VARCHAR);
CREATE TABLE IF NOT EXISTS run_rules (run_id INTEGER, rule_id VARCHAR, matched INTEGER);
CREATE OR REPLACE VIEW v_alerts_active AS
    SELECT rule_id, bq, severity, CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END AS severity_rank,
           record_key, customer_id, company_name, reason, first_raised_at, last_seen_at,
           times_raised, data_as_of
      FROM alerts WHERE status = 'active';
CREATE OR REPLACE VIEW v_alerts_by_rule AS
    SELECT rule_id, bq, severity, COUNT(*) FILTER (WHERE status = 'active') AS active,
           COUNT(*) FILTER (WHERE status = 'cleared') AS cleared
      FROM alerts GROUP BY rule_id, bq, severity;
CREATE OR REPLACE VIEW v_alerts_summary AS
    SELECT COUNT(*) FILTER (WHERE status = 'active') AS active,
           COUNT(*) FILTER (WHERE status = 'active' AND severity = 'high') AS active_high,
           COUNT(*) FILTER (WHERE status = 'active' AND severity = 'medium') AS active_medium,
           COUNT(*) FILTER (WHERE status = 'active' AND severity = 'low') AS active_low,
           (SELECT MAX(finished_at) FROM run_log WHERE status = 'ok') AS last_ok_run_at,
           (SELECT status FROM run_log ORDER BY run_id DESC LIMIT 1) AS last_run_status
      FROM alerts;
CREATE OR REPLACE VIEW v_alert_runs AS
    SELECT run_id, started_at, finished_at, data_as_of, status, rules_evaluated, alerts_active, alerts_new,
           alerts_cleared, error FROM run_log;
"""
SAFE_VIEW = re.compile(r"^v_[a-z0-9_]+$")


def load_rules(path: Path = ROOT / "config" / "alert_rules.yaml") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rules = yaml.safe_load(f)["rules"]
    ids = [r["id"] for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("alert rule ids must be unique")
    for r in rules:
        if not SAFE_VIEW.match(r["view"]):
            raise ValueError(f"rule {r['id']}: may only read a warehouse view (v_...), not {r['view']!r}")
        if ";" in r["where"] or "--" in r["where"]:
            raise ValueError(f"rule {r['id']}: the filter must be a single expression")
    return rules


def evaluate(warehouse_db: Path, rules: list[dict]) -> tuple[dict[str, dict], dict[str, int], dt.date]:
    """Current alerts {alert_id: row} from the warehouse, matched counts per rule, and the as-of date."""
    con = duckdb.connect(str(warehouse_db), read_only=True)
    try:
        as_of = con.execute("SELECT as_of_date FROM v_meta").fetchone()[0]
        names = dict(con.execute("SELECT customer_id, company_name FROM dim_customer").fetchall())
        current, counts = {}, {}
        for r in rules:
            cur = con.execute(f"SELECT * FROM {r['view']} WHERE {r['where']}")
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, x)) for x in cur.fetchall()]
            counts[r["id"]] = len(rows)
            keys = r["record_key"] if isinstance(r["record_key"], list) else [r["record_key"]]
            for row in rows:
                record = "|".join(str(row[k]) for k in keys)
                cust = row.get(r["customer"])
                current[f"{r['id']}|{record}"] = {
                    "rule_id": r["id"], "bq": r["bq"], "severity": r["severity"], "record_key": record,
                    "customer_id": cust, "company_name": names.get(cust), "reason": r["reason"].format(**row)}
    finally:
        con.close()
    return current, counts, as_of


def run(settings: dict, now: dt.datetime | None = None, rules: list[dict] | None = None,
        warehouse_db: Path | None = None, alerts_db: Path | None = None) -> dict:
    fixed_clock = now is not None                      # tests pass a clock; real runs use UTC now
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rules = rules if rules is not None else load_rules()
    wdb = warehouse_db or settings["warehouse_dir"] / warehouse_config()["database"]
    adb = alerts_db or settings["alerts_dir"] / "alerts.duckdb"
    adb.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(adb))
    try:
        con.execute(SCHEMA)
        run_id = con.execute("SELECT COALESCE(MAX(run_id), 0) + 1 FROM run_log").fetchone()[0]
        try:
            current, counts, as_of = evaluate(wdb, rules)
        except Exception as exc:                       # record the failed run, leave alerts as they were
            con.execute("INSERT INTO run_log VALUES (?, ?, ?, NULL, 'failed', ?, NULL, NULL, NULL, ?)",
                        [run_id, now, now, len(rules), f"{type(exc).__name__}: {exc}"])
            raise
        con.execute("BEGIN")
        existing = {r[0]: r[1] for r in con.execute("SELECT alert_id, status FROM alerts").fetchall()}
        new = reopened = 0
        for aid, a in current.items():
            if aid not in existing:
                new += 1
                con.execute("INSERT INTO alerts VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, 1, ?)",
                            [aid, a["rule_id"], a["bq"], a["severity"], a["record_key"], a["customer_id"],
                             a["company_name"], a["reason"], now, now, as_of])
            else:
                if existing[aid] == "cleared":
                    reopened += 1
                con.execute("""UPDATE alerts SET reason = ?, company_name = ?, last_seen_at = ?, data_as_of = ?,
                                      times_raised = times_raised + CASE WHEN status = 'cleared' THEN 1 ELSE 0 END,
                                      first_raised_at = CASE WHEN status = 'cleared' THEN ? ELSE first_raised_at END,
                                      status = 'active', cleared_at = NULL
                                WHERE alert_id = ?""", [a["reason"], a["company_name"], now, as_of, now, aid])
        gone = [aid for aid, st in existing.items() if st == "active" and aid not in current]
        for aid in gone:
            con.execute("UPDATE alerts SET status = 'cleared', cleared_at = ? WHERE alert_id = ?", [now, aid])
        for rid, n in counts.items():
            con.execute("INSERT INTO run_rules VALUES (?, ?, ?)", [run_id, rid, n])
        con.execute("INSERT INTO run_log VALUES (?, ?, ?, ?, 'ok', ?, ?, ?, ?, NULL)",
                    [run_id, now, now if fixed_clock else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
                     as_of, len(rules), len(current), new + reopened, len(gone)])
        con.execute("COMMIT")
    finally:
        con.close()
    return {"run_id": run_id, "active": len(current), "new": new + reopened, "cleared": len(gone), "by_rule": counts,
            "alerts_db": str(adb)}


def main() -> None:
    try:
        out = run(load_settings())
    except Exception as exc:
        print(f"Alert run failed (recorded in run_log): {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Alert run {out['run_id']}: {out['active']} active ({out['new']} new, {out['cleared']} cleared) "
          f"in {out['alerts_db']}")
    for rid, n in out["by_rule"].items():
        print(f"  {rid}: {n}")


if __name__ == "__main__":
    main()
