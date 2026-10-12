"""Mock shop REST API serving synthetic orders as JSON.

Run (binds to localhost only):
    uvicorn shop_api.app:app --host 127.0.0.1 --port 8765

GET /orders?page=1&page_size=100&from_date=2026-07-01&to_date=2026-09-30
    Orders sorted by order_id, filtered on the order date (inclusive at both ends).
    A page past the last one returns an empty list. Invalid parameters return 422.
GET /health

Reads data/raw/shop_orders.json (or the file named by SHOP_ORDERS_PATH). The file and every
response carry "synthetic": true. No ACC- identifiers exist in the shop data.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "raw" / "shop_orders.json"
MAX_PAGE_SIZE = 500


def create_app(orders_path: Path | str | None = None) -> FastAPI:
    path = Path(orders_path or os.environ.get("SHOP_ORDERS_PATH", DEFAULT_PATH))
    app = FastAPI(title="Northgate shop API (synthetic)", version="1.0")
    state: dict = {}

    def orders() -> list[dict]:
        if "orders" not in state:          # load lazily so the app starts without the file
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            state["note"] = payload.get("note", "")
            state["orders"] = sorted(payload["orders"], key=lambda o: o["order_id"])
        return state["orders"]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "synthetic": True}

    @app.get("/orders")
    def list_orders(
        page: int = Query(1, ge=1),
        page_size: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
        from_date: dt.date | None = Query(None, description="Earliest order date, inclusive"),
        to_date: dt.date | None = Query(None, description="Latest order date, inclusive"),
    ) -> dict:
        if from_date and to_date and from_date > to_date:
            raise HTTPException(status_code=422, detail="from_date must not be after to_date")
        selected = orders()
        if from_date or to_date:
            lo = (from_date or dt.date.min).isoformat()
            hi = (to_date or dt.date.max).isoformat()
            selected = [o for o in selected if lo <= o["order_date"][:10] <= hi]
        total = len(selected)
        start = (page - 1) * page_size
        return {
            "synthetic": True,
            "note": state.get("note", ""),
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": math.ceil(total / page_size) if total else 0,
            "orders": selected[start:start + page_size],
        }

    return app


app = create_app()
