
# Ensure transforms are imported so they register
import goldenflow.transforms.text  # noqa: F401
import goldenflow.transforms.phone  # noqa: F401
import goldenflow.transforms.dates  # noqa: F401
import goldenflow.transforms.categorical  # noqa: F401

from goldenflow.engine.profiler_bridge import ColumnProfile
from goldenflow.engine.selector import select_transforms


def test_select_transforms_for_email_column():
    profile = ColumnProfile(
        name="email", inferred_type="email", row_count=100,
        null_count=0, null_pct=0.0, unique_count=100, unique_pct=1.0,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "strip" in names
    assert "lowercase" not in names  # not auto_apply for string, but email-specific logic


def test_select_transforms_for_phone_column():
    profile = ColumnProfile(
        name="phone", inferred_type="phone", row_count=100,
        null_count=0, null_pct=0.0, unique_count=100, unique_pct=1.0,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "phone_e164" in names


def test_select_transforms_for_date_column():
    profile = ColumnProfile(
        name="signup_date", inferred_type="date", row_count=100,
        null_count=0, null_pct=0.0, unique_count=50, unique_pct=0.5,
    )
    selected = select_transforms(profile)
    names = [t.name for t in selected]
    assert "date_iso8601" in names


def test_select_no_transforms_for_unknown_type():
    profile = ColumnProfile(
        name="misc", inferred_type="unknown_xyz", row_count=100,
        null_count=0, null_pct=0.0, unique_count=100, unique_pct=1.0,
    )
    selected = select_transforms(profile)
    # Only universal transforms (string-type with auto_apply) should match
    assert all(t.auto_apply for t in selected)
