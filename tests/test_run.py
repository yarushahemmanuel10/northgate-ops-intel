"""run.py: deterministic from-scratch builds; single generators never touch data/raw."""
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from generator.config import load_config
from generator.run import build_all, update_readme

ROOT = Path(__file__).resolve().parents[1]


def _hashes(folder: Path) -> dict:
    if not folder.exists():
        return {}
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def test_two_runs_give_identical_files(tmp_path):
    cfg = load_config()
    build_all(cfg, tmp_path / "a")
    build_all(cfg, tmp_path / "b")
    for sub in ("data/clean", "data/raw", "data/ground_truth"):
        a, b = _hashes(tmp_path / "a" / sub), _hashes(tmp_path / "b" / sub)
        assert a and a == b, sub


def test_readme_update_is_idempotent(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n", encoding="utf-8")
    update_readme(readme)
    once = readme.read_text(encoding="utf-8")
    update_readme(readme)
    assert readme.read_text(encoding="utf-8") == once
    assert "data/raw/" in once


@pytest.mark.parametrize("module", ["customers", "crm", "helpdesk", "telecoms", "shop", "kb"])
def test_single_generators_never_change_data_raw(module):
    before = _hashes(ROOT / "data" / "raw") | {f"gt/{k}": v for k, v in _hashes(ROOT / "data" / "ground_truth").items()}
    subprocess.run([sys.executable, "-m", f"generator.{module}"], cwd=ROOT, check=True,
                   capture_output=True, timeout=120)
    after = _hashes(ROOT / "data" / "raw") | {f"gt/{k}": v for k, v in _hashes(ROOT / "data" / "ground_truth").items()}
    assert before == after


def test_main_runs_without_a_readme(tmp_path, monkeypatch, capsys):
    """D-046: the Docker image has no README.md; the generator must finish and say it skipped the table."""
    import generator.run as run
    monkeypatch.setattr(run, "ROOT", tmp_path)                       # a project root with no README.md
    monkeypatch.setattr(run, "build_all", lambda cfg: {"stub": True})  # no real data is written by this test
    monkeypatch.setattr(run, "summary", lambda cfg, out: "summary printed")
    run.main()
    out = capsys.readouterr().out
    assert "README not found" in out and "files table not updated" in out and "summary printed" in out
    assert not (tmp_path / "README.md").exists()
    assert run.update_readme(tmp_path / "README.md") is False
