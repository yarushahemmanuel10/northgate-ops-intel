"""Power BI-ready CSV exports of the warehouse (step 3.5, D-027).

Run:  python -m pipeline.warehouse.export   (after pipeline.warehouse.load)

One CSV per table and view in data/warehouse/exports/, rewritten on every run (old CSVs there
are removed first). UTF-8, header on the first line (no comment line, which Power BI would read
as the header), ISO dates and timestamps, '.' as the decimal mark, no thousands separators,
empty for missing values, rows in a fixed order. Revenue keeps full precision so totals match
the reconciliation. The synthetic-data notice is in README.txt beside the files.

ticket_text is never exported: it holds personal data. ticket_text_masked (step 6.1, D-043) is exported instead.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from pipeline.settings import load_settings
from pipeline.warehouse.load import load_config

NOT_EXPORTED = {"ticket_text"}       # personal data; the masked copy, ticket_text_masked, is exported
README = """SYNTHETIC DATA - fictional company Northgate IT Services Ltd; all names and figures invented.
No connection to any real company of a similar name.

Power BI-ready exports of data/warehouse/northgate.duckdb, one CSV per table or view.
Encoding UTF-8; first line is the header; dates YYYY-MM-DD, timestamps YYYY-MM-DD HH:MM:SS;
decimal mark '.', no thousands separators; an empty field is a missing value.
Revenue (v_revenue, v_contract_revenue_month) is at full precision: round only in visuals.
Relationships between the files: docs/powerbi_relationships.md.
ticket_text is not exported: it holds personal data. Use ticket_text_masked, where names, phone numbers, emails and business names are replaced by placeholders.
Regenerate with: python -m pipeline.run
"""


def export_all(db: Path, out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.csv"):
        old.unlink()
    con = duckdb.connect(str(db), read_only=True)
    counts = {}
    try:
        names = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name").fetchall()]
        for name in names:
            if name in NOT_EXPORTED:
                continue
            path = out_dir / f"{name}.csv"
            con.execute(f"COPY (SELECT * FROM {name} ORDER BY ALL) TO '{path.as_posix()}' "
                        "(HEADER, DELIMITER ',', DATEFORMAT '%Y-%m-%d', TIMESTAMPFORMAT '%Y-%m-%d %H:%M:%S')")
            counts[name] = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    finally:
        con.close()
    (out_dir / "README.txt").write_text(README, encoding="utf-8")
    return counts


def run(settings: dict, cfg: dict) -> dict[str, int]:
    wd = settings["warehouse_dir"]
    return export_all(wd / cfg["database"], wd / "exports")


def main() -> None:
    settings = load_settings()
    for name, n in run(settings, load_config()).items():
        print(f"  {name}.csv: {n}")


if __name__ == "__main__":
    main()
