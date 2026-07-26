"""from_splink accepts a live Splink Linker (no manual JSON export).

A Linker holds its settings in memory; ``from_splink`` extracts them via
``linker.misc.save_model_to_json(out_path=None)`` (splink 4) -- duck-typed, so
no splink import is needed to convert, and the extraction path is testable with
a lightweight fake. One real end-to-end test is guarded by
``importorskip("splink")``.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.from_splink import (
    SplinkConversionError,
    _extract_linker_settings,
    from_splink,
)

_SETTINGS = {
    "link_type": "dedupe_only",
    "unique_id_column_name": "unique_id",
    "comparisons": [
        {
            "output_column_name": "first_name",
            "comparison_levels": [
                {"sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
                 "is_null_level": True},
                {"sql_condition": '"first_name_l" = "first_name_r"'},
                {"sql_condition": 'jaro_winkler_similarity("first_name_l","first_name_r") >= 0.9'},
                {"sql_condition": "ELSE"},
            ],
        },
    ],
    "blocking_rules_to_generate_predictions": ["l.surname = r.surname"],
}


class _FakeMisc:
    """Mimics splink 4's ``linker.misc``."""

    def __init__(self, settings: dict) -> None:
        self._settings = settings
        self.calls: list[dict] = []

    def save_model_to_json(self, out_path=None, overwrite: bool = False) -> dict:
        self.calls.append({"out_path": out_path, "overwrite": overwrite})
        # A real Linker writes nothing when out_path is None and returns the dict.
        assert out_path is None, "from_splink must not trigger a disk write"
        return dict(self._settings)


class _FakeLinker:
    """Duck-typed splink 4 Linker: settings via ``.misc.save_model_to_json``."""

    def __init__(self, settings: dict) -> None:
        self.misc = _FakeMisc(settings)


class _FakeDirectLinker:
    """A shape exposing ``save_model_to_json`` directly on the object."""

    def __init__(self, settings: dict) -> None:
        self._settings = settings

    def save_model_to_json(self, out_path=None) -> dict:
        return dict(self._settings)


# ── extraction ───────────────────────────────────────────────────────────────


def test_extract_from_misc_shape() -> None:
    linker = _FakeLinker(_SETTINGS)
    extracted = _extract_linker_settings(linker)
    assert extracted is not None
    assert extracted["comparisons"] == _SETTINGS["comparisons"]
    # extraction goes through the no-write path
    assert linker.misc.calls == [{"out_path": None, "overwrite": False}]


def test_extract_from_direct_method_shape() -> None:
    extracted = _extract_linker_settings(_FakeDirectLinker(_SETTINGS))
    assert extracted is not None
    assert "comparisons" in extracted


@pytest.mark.parametrize("obj", [42, "not a linker", object(), {"comparisons": []}])
def test_extract_returns_none_for_non_linker(obj) -> None:
    # dicts are handled by _load_settings directly, not the linker extractor;
    # a bare object / scalar has no save_model_to_json -> None.
    assert _extract_linker_settings(obj) is None


def test_extract_returns_none_when_serializer_raises() -> None:
    class _Broken:
        def save_model_to_json(self, out_path=None):
            raise RuntimeError("boom")

    assert _extract_linker_settings(_Broken()) is None


def test_extract_returns_none_when_serializer_returns_non_dict() -> None:
    class _WrongReturn:
        def save_model_to_json(self, out_path=None):
            return "not a dict"

    assert _extract_linker_settings(_WrongReturn()) is None


# ── from_splink integration ──────────────────────────────────────────────────


def test_from_splink_accepts_fake_linker() -> None:
    conv = from_splink(_FakeLinker(_SETTINGS))
    fields = conv.config.matchkeys[0].fields
    assert [f.field for f in fields] == ["first_name"]
    assert fields[0].scorer == "jaro_winkler"


def test_from_splink_accepts_direct_method_linker() -> None:
    conv = from_splink(_FakeDirectLinker(_SETTINGS))
    assert conv.config.matchkeys[0].fields[0].field == "first_name"


def test_from_splink_does_not_mutate_extracted_settings() -> None:
    # The extractor copies; converting must not mutate the linker's own dict.
    linker = _FakeLinker(_SETTINGS)
    from_splink(linker)
    assert linker.misc._settings == _SETTINGS


def test_unsupported_source_error_mentions_linker() -> None:
    with pytest.raises(SplinkConversionError, match="Splink Linker"):
        from_splink(42)


# ── real end-to-end (needs splink) ───────────────────────────────────────────


def test_from_splink_accepts_real_linker() -> None:
    pytest.importorskip("splink")
    pd = pytest.importorskip("pandas")
    from splink import DuckDBAPI, Linker

    df = pd.DataFrame(
        {
            "unique_id": [0, 1, 2, 3],
            "first_name": ["john", "jon", "al", "al"],
            "surname": ["smith", "smith", "lee", "lee"],
        }
    )
    linker = Linker(df, dict(_SETTINGS), DuckDBAPI())
    conv = from_splink(linker)
    assert conv.config.matchkeys[0].fields[0].field == "first_name"
    assert conv.config.blocking.strategy == "static"
