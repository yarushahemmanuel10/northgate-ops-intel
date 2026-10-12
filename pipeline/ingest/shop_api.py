"""Connector for the online shop: pages through the shop REST API.

There is deliberately no fallback to reading the shop's JSON file: if the API cannot be
reached, ingestion stops with a clear message. JSON numbers are kept as their exact text.
"""
from __future__ import annotations

import json

import httpx

from pipeline.ingest.common import LINEAGE, Rejected, SchemaError
from pipeline.ingest.schemas import SHOP_LINE, SHOP_ORDER

ORDER_COLUMNS = [c for c in SHOP_ORDER if c != "lines"] + ["line_count", "_source_page"] + LINEAGE
LINE_COLUMNS = ["order_id", "line_no"] + SHOP_LINE + ["_source_page"] + LINEAGE


class ShopApiUnavailable(RuntimeError):
    pass


def _text(v) -> str:
    return "" if v is None else str(v)


def fetch_orders(client: httpx.Client, base_url: str, page_size: int, ingested_at: str,
                 rejected: Rejected) -> tuple[list[dict], list[dict], dict]:
    orders, lines = [], []
    page, total = 1, None
    while True:
        try:
            resp = client.get(f"{base_url}/orders", params={"page": page, "page_size": page_size})
        except httpx.TransportError as exc:
            raise ShopApiUnavailable(
                f"Cannot reach the shop API at {base_url} ({exc.__class__.__name__}). Start it with:\n"
                f"  uv run uvicorn shop_api.app:app --host 127.0.0.1 --port 8765\n"
                f"Ingestion does not fall back to reading the shop file.") from None
        if resp.status_code != 200:
            raise ShopApiUnavailable(f"Shop API returned HTTP {resp.status_code} for page {page}: {resp.text[:200]}")
        try:
            body = json.loads(resp.text, parse_float=str, parse_int=str)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"Shop API page {page} is not valid JSON: {exc}") from None
        if body.get("synthetic") is not True:
            raise SchemaError("Shop API response is not marked synthetic: true")
        total = int(body["total"])
        batch = body.get("orders", [])
        if not batch:
            break
        for rec in batch:
            missing = [c for c in SHOP_ORDER if c not in rec] if isinstance(rec, dict) else SHOP_ORDER
            if missing:
                rejected.add("shop", "/orders", f"page {page}", f"missing fields {missing}", json.dumps(rec)[:500])
                continue
            lineage = dict(zip(LINEAGE, ("shop", "/orders", rec["order_id"], ingested_at)))
            row = {c: _text(rec[c]) for c in SHOP_ORDER if c != "lines"}
            row.update(line_count=str(len(rec["lines"])), _source_page=str(page), **lineage)
            orders.append(row)
            for i, ln in enumerate(rec["lines"], start=1):
                lines.append({"order_id": rec["order_id"], "line_no": str(i),
                              **{c: _text(ln.get(c)) for c in SHOP_LINE}, "_source_page": str(page), **lineage})
        page += 1
    if total is not None and len(orders) + len([r for r in rejected.rows if r["source_system"] == "shop"]) != total:
        raise SchemaError(f"Shop API reported {total} orders but {len(orders)} were read")
    return orders, lines, {"pages": page - 1, "total": total}
