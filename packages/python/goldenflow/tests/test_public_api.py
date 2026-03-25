"""Verify the public API surface is importable and consistent."""

import goldenflow


def test_version_exists():
    assert hasattr(goldenflow, "__version__")
    assert isinstance(goldenflow.__version__, str)


def test_all_exports_importable():
    for name in goldenflow.__all__:
        assert hasattr(goldenflow, name), f"{name} in __all__ but not importable"
