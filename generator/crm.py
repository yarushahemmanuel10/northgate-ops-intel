"""CRM workbook (Excel) built from the ground truth.

Run:  python -m generator.crm
Writes data/raw/crm_accounts.xlsx with sheets README, Accounts and Contract Lines.

This module writes the *clean* workbook. Deliberate quality problems (QI-03, QI-04,
QI-05, QI-08, QI-11) are injected by a later step, not here.

Only columns a real CRM would hold are written. Ground-truth-only fields (at_risk,
is_new_in_window, lookalike_group, lookalike_kind, revenue_source, business_lines,
shop_annual_budget) never appear in the workbook.
"""
from __future__ import annotations

import datetime as dt
import re
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font

from generator.config import load_config
from generator.customers import SYNTHETIC_HEADER, generate, make_rngs

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "clean"   # clean output; only generator.messiness writes data/raw (D-015)
WORKBOOK_NAME = "crm_accounts.xlsx"

# Ground-truth column -> header as typed in the CRM workbook.
ACCOUNT_COLUMNS = {
    "account_id": "Account ID",
    "company_name": "Company Name",
    "sector": "Sector",
    "account_manager": "Account Manager",
    "customer_since": "Customer Since",
    "status": "Status",
    "closed_date": "Closed Date",
}
LINE_COLUMNS = {
    "line_id": "Line ID",
    "account_id": "Account ID",
    "business_line": "Business Line",
    "annual_value": "Annual Value (GBP)",
    "contract_start": "Contract Start",
    "term_years": "Term (Years)",
    "renewal_date": "Renewal Date",
    "end_date": "End Date",
}
GROUND_TRUTH_ONLY = ("at_risk", "is_new_in_window", "lookalike_group", "lookalike_kind",
                     "revenue_source", "business_lines", "shop_annual_budget")

# Fixed file metadata so the same seed gives a byte-identical workbook (openpyxl and
# zipfile would otherwise stamp the current time).
FIXED_TIMESTAMP = dt.datetime(2026, 9, 30, 18, 0, 0)
FIXED_ISO = FIXED_TIMESTAMP.strftime("%Y-%m-%dT%H:%M:%SZ")
FIXED_ZIP_TIME = FIXED_TIMESTAMP.timetuple()[:6]

README_LINES = [
    "SYNTHETIC DATA",
    "",
    "This workbook belongs to a portfolio prototype. Northgate IT Services Ltd is a made-up company.",
    "Every name, date and value in it is invented. Company names are fictional and may coincidentally",
    "resemble real businesses; they were not checked against any company register.",
    "",
    "Sheets:",
    "  Accounts        - one row per customer account (including customers who only buy from the shop)",
    "  Contract Lines  - one row per business line sold to a customer (BL1-BL4; shop purchases have no contract line)",
    "",
    "On each data sheet, row 1 is a synthetic-data marker and row 2 holds the column headers.",
    "Business lines: BL1 Managed IT support, BL2 Telecoms, BL3 Cloud hosting, BL4 Bespoke software.",
]


def _cell(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "item"):          # numpy scalar -> Python scalar
        return value.item()
    return value


def _write_data_sheet(wb: Workbook, title: str, df: pd.DataFrame, columns: dict[str, str]) -> None:
    ws = wb.create_sheet(title)
    ws.append([SYNTHETIC_HEADER])
    ws.append(list(columns.values()))
    for cell in ws[2]:
        cell.font = Font(bold=True)
    date_cols = []
    for row in df[list(columns)].itertuples(index=False):
        ws.append([_cell(v) for v in row])
    for j, col in enumerate(columns, start=1):
        if df[col].map(lambda v: isinstance(v, dt.date)).any():
            date_cols.append(j)
    for j in date_cols:
        for (cell,) in ws.iter_rows(min_row=3, min_col=j, max_col=j):
            if cell.value is not None:
                cell.number_format = "yyyy-mm-dd"
    for j, header in enumerate(columns.values(), start=1):
        ws.column_dimensions[ws.cell(row=2, column=j).column_letter].width = max(12, len(header) + 2)


def _normalise_zip(path: Path) -> None:
    """Rewrite the .xlsx with fixed entry timestamps and fixed document dates."""
    with zipfile.ZipFile(path) as zin:
        entries = [(info, zin.read(info.filename)) for info in zin.infolist()]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for info, data in entries:
            if info.filename == "docProps/core.xml":
                text = data.decode("utf-8")
                text = re.sub(r"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:\2>)",
                              rf"\g<1>{FIXED_ISO}\g<3>", text)
                data = text.encode("utf-8")
            fixed = zipfile.ZipInfo(info.filename, date_time=FIXED_ZIP_TIME)
            fixed.compress_type = zipfile.ZIP_DEFLATED
            fixed.external_attr = 0o644 << 16
            zout.writestr(fixed, data)


def write_crm_workbook(customers: pd.DataFrame, lines: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    readme = wb.active
    readme.title = "README"
    for line in README_LINES:
        readme.append([line])
    readme["A1"].font = Font(bold=True)
    readme.column_dimensions["A"].width = 110
    _write_data_sheet(wb, "Accounts", customers, ACCOUNT_COLUMNS)
    _write_data_sheet(wb, "Contract Lines", lines, LINE_COLUMNS)
    wb.properties.creator = "Synthetic data generator"
    wb.properties.lastModifiedBy = "Synthetic data generator"
    wb.properties.title = "Northgate CRM workbook (synthetic)"
    wb.properties.created = FIXED_TIMESTAMP
    wb.properties.modified = FIXED_TIMESTAMP
    wb.save(path)
    _normalise_zip(path)
    return path


def build(cfg: dict[str, Any], out_dir: Path = RAW_DIR, seed: int | None = None) -> Path:
    rng, fake = make_rngs(cfg["seed"] if seed is None else seed)
    customers, lines = generate(cfg, rng, fake)
    return write_crm_workbook(customers, lines, out_dir / WORKBOOK_NAME)


def main() -> None:
    path = build(load_config())
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
