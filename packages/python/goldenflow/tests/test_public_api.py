"""Verify the public API surface is importable and consistent."""

import goldenflow


def test_version_exists():
    assert hasattr(goldenflow, "__version__")
    assert isinstance(goldenflow.__version__, str)


def test_all_exports_importable():
    for name in goldenflow.__all__:
        assert hasattr(goldenflow, name), f"{name} in __all__ but not importable"


def test_expanded_exports_present():
    """Verify the ~25 additional exports added in the CLI polish pass."""
    expected = [
        # Engine — manifest
        "Manifest",
        "TransformRecord",
        "TransformError",
        # Engine — profiler
        "DatasetProfile",
        "ColumnProfile",
        # Engine — selector
        "select_transforms",
        # Engine — differ
        "diff_dataframes",
        "DiffResult",
        # Transforms registry
        "TransformInfo",
        "register_transform",
        "get_transform",
        "list_transforms",
        "parse_transform_name",
        # Mapping
        "SchemaMapper",
        "ColumnMapping",
        # Config
        "load_config",
        "save_config",
        "merge_configs",
        "learn_config",
        # Domains
        "DomainPack",
        "load_domain",
        # Connectors
        "read_file",
        "write_file",
    ]
    for name in expected:
        assert name in goldenflow.__all__, f"{name} missing from __all__"
        assert hasattr(goldenflow, name), f"{name} in __all__ but not importable"
