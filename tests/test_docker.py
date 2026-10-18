"""Docker files (step 6.5, D-046). Docker cannot run in the build workspace, so these checks are static, plus a
run of every dashboard page from exactly the files the dashboard image copies."""
import os
import re
import shutil

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from pipeline.settings import ROOT, load_settings
from tests.test_dashboard import db  # noqa: F401  (fixture)

DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
IGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()


def stage(name):
    m = re.search(rf"^FROM \S+ AS {name}\n(.*?)(?=^FROM |\Z)", DOCKERFILE, flags=re.M | re.S)
    assert m, name
    return m.group(1)


def copies(name):
    out = []
    for line in stage(name).splitlines():
        if line.startswith("COPY "):
            out += line.split()[1:-1]
    return out


def test_base_image_is_pinned_by_a_multi_arch_digest():
    m = re.search(r"^ARG PYTHON_IMAGE=python:3\.12-slim-bookworm@sha256:([0-9a-f]{64})$", DOCKERFILE, flags=re.M)
    assert m and "FROM ${PYTHON_IMAGE} AS base" in DOCKERFILE
    assert re.search(r"pip install --no-cache-dir uv==\d+\.\d+\.\d+", DOCKERFILE)
    assert "uv sync --frozen --no-dev" in DOCKERFILE


def test_final_images_run_as_a_non_root_user():
    for name in ("pipeline", "dashboard"):
        assert re.search(r"^USER app$", stage(name), flags=re.M), name
    assert "--uid 10001" in DOCKERFILE


def test_secrets_and_host_data_never_enter_an_image():
    assert {".env", ".env.*", "data/", ".venv/", ".venv-linux/", ".git/"} <= set(IGNORE)
    assert [x for x in IGNORE if x.startswith("!")] == ["!.env.example"]
    for name in ("pipeline", "dashboard"):
        assert not any(c.startswith((".env", "data")) for c in copies(name)), name


def test_dashboard_image_has_no_generator_answer_key_or_shop_api():
    c = copies("dashboard")
    assert not any(x.startswith(("generator", "shop_api", "eval", "tests", "knowledge_base")) or x == "config/"
                   or "generator.yaml" in x for x in c), c


def test_compose_uses_named_volumes_only_and_least_privilege():
    services, volumes = COMPOSE["services"], COMPOSE["volumes"]
    for name, svc in services.items():
        for v in svc.get("volumes", []):
            src = v.split(":")[0]
            assert src in volumes, f"{name} mounts {src}, which is not a named volume (host folders are never used)"
    dash = sorted(services["dashboard"]["volumes"])
    assert dash == ["alerts:/app/data/alerts:ro", "audit:/app/data/audit", "warehouse:/app/data/warehouse:ro"]
    assert not any(v.startswith("work:") for v in services["dashboard"]["volumes"])   # no clean/, ground_truth/, raw/
    assert all(v.endswith(":ro") for v in services["shop_api"]["volumes"])


def test_ports_are_published_on_localhost_only():
    published = [(n, p) for n, s in COMPOSE["services"].items() for p in s.get("ports", [])]
    assert published == [("dashboard", "127.0.0.1:8501:8501")]


def test_startup_order_waits_for_a_healthy_shop_api():
    s = COMPOSE["services"]
    assert "/health" in " ".join(s["shop_api"]["healthcheck"]["test"])
    assert s["shop_api"]["depends_on"] == {"generate": {"condition": "service_completed_successfully"}}
    assert s["pipeline"]["depends_on"] == {"shop_api": {"condition": "service_healthy"}}
    assert s["pipeline"]["environment"]["NORTHGATE_SHOP_API_URL"] == "http://shop_api:8765"


def test_shop_api_address_can_be_overridden(monkeypatch):
    assert load_settings()["shop_api"]["base_url"] == "http://127.0.0.1:8765"
    monkeypatch.setenv("NORTHGATE_SHOP_API_URL", "http://shop_api:8765")
    assert load_settings()["shop_api"]["base_url"] == "http://shop_api:8765"


def test_every_dashboard_page_runs_from_the_dashboard_image_files(db, tmp_path, monkeypatch):  # noqa: F811
    """Copy exactly what the dashboard stage copies, then open every page as the operations director."""
    sim = tmp_path / "app"
    for line in stage("dashboard").splitlines():
        if not line.startswith("COPY "):
            continue
        *srcs, dest = line.split()[1:]
        for src in srcs:
            target = sim / dest / (os.path.basename(src) if not src.endswith("/") else "")
            if src.endswith("/"):
                shutil.copytree(ROOT / src, sim / dest, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / src, target)
    assert not (sim / "generator").exists() and not (sim / "config" / "generator.yaml").exists()
    monkeypatch.setenv("NORTHGATE_DB", str(db))
    monkeypatch.setenv("NORTHGATE_ALERTS_DB", str(db.parent / "alerts.duckdb"))
    monkeypatch.setenv("NORTHGATE_ROLE", "operations_director")
    monkeypatch.setenv("NORTHGATE_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.syspath_prepend(str(sim))
    for name in list(__import__("sys").modules):            # import pipeline/dashboard from the copied files
        if name == "pipeline" or name.startswith(("pipeline.", "dashboard")):
            monkeypatch.delitem(__import__("sys").modules, name)
    for page in ("overview", "service", "customers", "data_quality", "alerts"):
        at = AppTest.from_file(str(sim / "dashboard" / "pages" / f"{page}.py"), default_timeout=60).run()
        assert not at.exception, (page, [e.value for e in at.exception])


def test_only_one_service_builds_the_shared_pipeline_image():
    """D-046 fix: three services building the same tag raced ("image already exists")."""
    s = COMPOSE["services"]
    builders = [n for n, svc in s.items() if svc.get("image") == "northgate-pipeline:local" and "build" in svc]
    assert builders == ["pipeline"] and s["pipeline"]["build"]["target"] == "pipeline"
    for n in ("generate", "shop_api"):
        assert "build" not in s[n] and s[n]["image"] == "northgate-pipeline:local" and s[n]["pull_policy"] == "never"
    tags = [svc["image"] for svc in s.values() if "build" in svc]
    assert len(tags) == len(set(tags))                      # no tag is built by two services


def test_copied_files_are_readable_by_the_app_user_and_writable_only_in_data():
    """D-046 fix: COPY keeps host modes (600 on the author's Mac), which uid 10001 could not read."""
    for name in ("pipeline", "dashboard"):
        lines = [l.strip() for l in stage(name).splitlines() if l.strip() and not l.strip().startswith("#")]
        last_copy = max(i for i, l in enumerate(lines) if l.startswith("COPY "))
        run = next(i for i, l in enumerate(lines) if l.startswith("RUN chmod -R a+rX,go-w /app"))
        user = next(i for i, l in enumerate(lines) if l == "USER app")
        assert last_copy < run < user, name                 # permissions fixed after every COPY, before dropping root
        chowns = re.findall(r"chown\s+-R\s+app:app\s+(\S+)", stage(name))
        assert chowns == ["/app/data"], (name, chowns)      # the app user owns nothing but the data folders
        assert "--chown" not in stage(name), name
    assert "chmod -R a+rX,go-w /opt/venv" in stage("base")
