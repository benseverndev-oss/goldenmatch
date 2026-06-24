"""Guards for the consolidated benchmark registry + dispatcher (repo hygiene).

`.github/benchmarks/registry.yml` is the single source of truth for both the
`bench.yml` workflow and `scripts/bench.py`. These tests keep the three in
lockstep: the registry stays schema-valid, every script it names exists, the
dispatcher builds the right command, and the workflow's `suite` choice options
never drift from the registry keys (the "add a row, not a workflow" promise).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml


def _repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".github" / "benchmarks" / "registry.yml").is_file():
            return parent
    pytest.skip("repo-root benchmark registry not found (packaged checkout)")


ROOT = _repo_root()
REGISTRY = ROOT / ".github" / "benchmarks" / "registry.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "bench.yml"
DISPATCHER = ROOT / "scripts" / "bench.py"


def _load_dispatcher():
    spec = importlib.util.spec_from_file_location("_bench_cli", DISPATCHER)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_registry_is_schema_valid():
    reg = _load_dispatcher().load_registry(REGISTRY)
    assert reg, "registry is empty"
    for name, entry in reg.items():
        assert entry["desc"], f"{name}: missing desc"
        assert entry["script"], f"{name}: missing script"
        assert entry["install"] in ("uv", "pip")
        assert isinstance(entry["env"], dict)


def test_every_registered_script_exists():
    reg = _load_dispatcher().load_registry(REGISTRY)
    missing = []
    for name, entry in reg.items():
        # bench_perceptual/run.py etc. live under a workdir; resolve from root.
        script = ROOT / entry["workdir"] / entry["script"]
        if not script.is_file():
            missing.append(f"{name} -> {entry['workdir']}/{entry['script']}")
    assert not missing, "registered scripts not found on disk:\n" + "\n".join(missing)


def test_build_command_uv_threads_with_deps():
    cli = _load_dispatcher()
    reg = cli.load_registry(REGISTRY)
    entry = reg["lsh-recall"]  # uv install + with: [datasets, sentence-transformers]
    cmd = cli.build_command(entry, [])
    assert cmd[:2] == ["uv", "run"]
    assert "--with" in cmd and "datasets" in cmd and "sentence-transformers" in cmd
    assert "python" in cmd and entry["script"] in cmd


def test_build_command_pip_is_plain_python():
    cli = _load_dispatcher()
    reg = cli.load_registry(REGISTRY)
    entry = reg["prepared-store"]  # install: pip
    cmd = cli.build_command(entry, ["--rows", "1000"])
    assert cmd[0] == "python"
    assert cmd[-2:] == ["--rows", "1000"]
    assert "uv" not in cmd


def test_unknown_field_is_rejected(tmp_path):
    cli = _load_dispatcher()
    bad = tmp_path / "bad.yml"
    bad.write_text("foo:\n  desc: x\n  script: scripts/x.py\n  bogus: 1\n")
    with pytest.raises(ValueError, match="unknown field"):
        cli.load_registry(bad)


def test_workflow_choices_match_registry_keys():
    """No-drift: the workflow's `suite` choice options must equal the registry
    keys exactly — so a new bench row can't be silently un-dispatchable, and a
    removed bench can't leave a dead choice."""
    reg = _load_dispatcher().load_registry(REGISTRY)
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the bare `on:` key as boolean True.
    triggers = wf.get("on") or wf.get(True)
    options = triggers["workflow_dispatch"]["inputs"]["suite"]["options"]
    assert set(options) == set(reg), (
        "bench.yml suite options drifted from registry:\n"
        f"  only in workflow: {sorted(set(options) - set(reg))}\n"
        f"  only in registry: {sorted(set(reg) - set(options))}"
    )
