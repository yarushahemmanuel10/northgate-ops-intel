"""Mock shop API: paging order, boundaries, date filter, validation, no leakage."""
import json

import pytest
from fastapi.testclient import TestClient

from generator.config import load_config
from generator.shop import build
from shop_api.app import MAX_PAGE_SIZE, create_app


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    cfg = load_config()
    raw_dir = tmp_path_factory.mktemp("raw")
    _, _, payload, _ = build(cfg, raw_dir, tmp_path_factory.mktemp("truth"))
    client = TestClient(create_app(raw_dir / "shop_orders.json"))
    return client, payload


def test_health(setup):
    client, _ = setup
    assert client.get("/health").json() == {"status": "ok", "synthetic": True}


def test_first_page(setup):
    client, payload = setup
    body = client.get("/orders", params={"page": 1, "page_size": 50}).json()
    ids = sorted(o["order_id"] for o in payload["orders"])
    assert body["synthetic"] is True
    assert body["total"] == len(ids) and body["total_pages"] == -(-len(ids) // 50)
    assert [o["order_id"] for o in body["orders"]] == ids[:50]


def test_pages_cover_everything_in_order(setup):
    client, payload = setup
    seen = []
    page = 1
    while True:
        body = client.get("/orders", params={"page": page, "page_size": MAX_PAGE_SIZE}).json()
        if not body["orders"]:
            break
        seen += [o["order_id"] for o in body["orders"]]
        page += 1
    assert seen == sorted(o["order_id"] for o in payload["orders"])


def test_last_page_and_empty_page(setup):
    client, payload = setup
    n = len(payload["orders"])
    last = -(-n // 100)
    body = client.get("/orders", params={"page": last, "page_size": 100}).json()
    assert len(body["orders"]) == n - (last - 1) * 100
    empty = client.get("/orders", params={"page": last + 1, "page_size": 100})
    assert empty.status_code == 200 and empty.json()["orders"] == []


def test_date_filter_boundaries_inclusive(setup):
    client, payload = setup
    dates = sorted({o["order_date"][:10] for o in payload["orders"]})
    day = dates[len(dates) // 2]
    expected = sorted(o["order_id"] for o in payload["orders"] if o["order_date"][:10] == day)
    body = client.get("/orders", params={"from_date": day, "to_date": day, "page_size": MAX_PAGE_SIZE}).json()
    assert [o["order_id"] for o in body["orders"]] == expected and expected
    # from only / to only
    after = client.get("/orders", params={"from_date": dates[-1], "page_size": MAX_PAGE_SIZE}).json()
    assert all(o["order_date"][:10] == dates[-1] for o in after["orders"]) and after["total"] > 0
    before = client.get("/orders", params={"to_date": dates[0], "page_size": MAX_PAGE_SIZE}).json()
    assert all(o["order_date"][:10] == dates[0] for o in before["orders"]) and before["total"] > 0
    outside = client.get("/orders", params={"from_date": "2030-01-01"}).json()
    assert outside["total"] == 0 and outside["orders"] == []


@pytest.mark.parametrize("params", [
    {"page": 0}, {"page": -1}, {"page": "abc"}, {"page_size": 0}, {"page_size": MAX_PAGE_SIZE + 1},
    {"from_date": "2026-13-01"}, {"to_date": "yesterday"},
    {"from_date": "2026-09-30", "to_date": "2026-09-01"},
])
def test_bad_parameters_get_422(setup, params):
    client, _ = setup
    assert client.get("/orders", params=params).status_code == 422


def test_no_account_ids_in_responses(setup):
    client, _ = setup
    text = json.dumps(client.get("/orders", params={"page_size": MAX_PAGE_SIZE}).json())
    assert "ACC-" not in text
