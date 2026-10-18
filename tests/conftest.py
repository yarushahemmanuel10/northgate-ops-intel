"""Shared test setup: every test writes audit entries to a temporary folder, never to data/audit (D-045)."""
import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _audit_to_temp(tmp_path_factory):
    old = os.environ.get("NORTHGATE_AUDIT_DIR")
    os.environ["NORTHGATE_AUDIT_DIR"] = str(tmp_path_factory.mktemp("audit"))
    yield
    if old is None:
        os.environ.pop("NORTHGATE_AUDIT_DIR", None)
    else:
        os.environ["NORTHGATE_AUDIT_DIR"] = old
