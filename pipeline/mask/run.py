"""Build the masked ticket text inside the warehouse (step 6.1, D-043).

Called by pipeline.warehouse.load after the base tables are written. Reads ticket_text and the warehouse's own
name lists (account managers, agents, business names, review-queue spellings), writes ticket_text_masked and the
view v_ticket_masked. The original ticket_text stays in the warehouse only: it is never exported, never shown on
the dashboard and never sent to the assistant; everything that leaves the warehouse uses the masked table.
"""
from __future__ import annotations

from pipeline.mask.text import Lists, mask


def lists_from(con) -> Lists:
    staff = {r[0] for r in con.execute(
        "SELECT account_manager FROM dim_customer WHERE account_manager IS NOT NULL "
        "UNION SELECT agent FROM fact_ticket WHERE agent IS NOT NULL").fetchall()}
    customers = {r[0] for r in con.execute(
        "SELECT company_name FROM dim_customer WHERE company_name IS NOT NULL AND NOT is_unknown "
        "UNION SELECT raw_name FROM dq_review_record WHERE raw_name IS NOT NULL").fetchall()}
    return Lists(staff={s.strip() for s in staff if s.strip()}, customers={c.strip() for c in customers if c.strip()})


def create_masked(con) -> int:
    lists = lists_from(con)
    rows = []
    for tid, desc, notes in con.execute(
            "SELECT ticket_id, description, resolution_notes FROM ticket_text ORDER BY ticket_id").fetchall():
        d, ds = mask(desc, lists)
        n, ns = mask(notes, lists)
        kinds = [s.kind for s in ds + ns]
        rows.append((tid, d, n, kinds.count("NAME"), kinds.count("PHONE"), kinds.count("EMAIL"), kinds.count("CUSTOMER")))
    con.execute("CREATE TABLE ticket_text_masked (ticket_id VARCHAR PRIMARY KEY, description_masked VARCHAR, "
                "resolution_notes_masked VARCHAR, names_masked INTEGER, phones_masked INTEGER, emails_masked INTEGER, "
                "customers_masked INTEGER)")
    con.executemany("INSERT INTO ticket_text_masked VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute("""CREATE VIEW v_ticket_masked AS
        SELECT t.ticket_id, t.customer_id, t.priority, t.category, t.opened_at, t.closed_at,
               m.description_masked AS problem_text_masked, m.resolution_notes_masked AS resolution_text_masked
          FROM fact_ticket t JOIN ticket_text_masked m USING (ticket_id)""")
    return len(rows)
