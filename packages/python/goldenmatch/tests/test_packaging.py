"""Packaging guards for goldenmatch's pyproject.toml.

Locks the contract that `goldenmatch-native` ships as a marker-guarded core
dependency (so `pip install goldenmatch` auto-installs the abi3 kernel where
prebuilt wheels exist) while remaining available via the back-compat `[native]`
extra.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def _load_pyproject() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def test_native_is_marker_guarded_core_dependency() -> None:
    """goldenmatch-native is a core dep guarded by a PEP 508 platform marker."""
    project = _load_pyproject()["project"]
    deps = project["dependencies"]

    native_specs = [d for d in deps if d.startswith("goldenmatch-native")]
    assert len(native_specs) == 1, (
        f"expected exactly one goldenmatch-native core dependency, got {native_specs}"
    )

    spec = native_specs[0]
    # Marker must scope the dep to platforms that have prebuilt abi3 wheels.
    assert ";" in spec, f"goldenmatch-native core dep must carry a marker: {spec!r}"
    assert "sys_platform == 'darwin'" in spec
    assert "platform_machine == 'aarch64'" in spec


def test_native_extra_still_present() -> None:
    """The [native] extra remains as a back-compat alias."""
    optional = _load_pyproject()["project"]["optional-dependencies"]
    assert "native" in optional
    assert any(d.startswith("goldenmatch-native") for d in optional["native"])
