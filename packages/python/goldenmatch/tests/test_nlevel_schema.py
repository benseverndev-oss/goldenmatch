"""N-level MatchkeyField schema tests (Splink-converter Stage 1)."""
import pytest
from goldenmatch.config.schemas import MatchkeyField


def test_level_thresholds_accepted():
    f = MatchkeyField(field="first_name", scorer="jaro_winkler",
                      levels=4, level_thresholds=[1.0, 0.92, 0.88])
    assert f.level_thresholds == [1.0, 0.92, 0.88]


def test_level_thresholds_wrong_length_rejected():
    with pytest.raises(ValueError, match="level_thresholds"):
        MatchkeyField(field="x", scorer="jaro_winkler",
                      levels=4, level_thresholds=[1.0, 0.9])  # needs levels-1 = 3


def test_level_thresholds_must_descend():
    with pytest.raises(ValueError, match="descending"):
        MatchkeyField(field="x", scorer="jaro_winkler",
                      levels=3, level_thresholds=[0.8, 0.9])


def test_level_thresholds_range():
    with pytest.raises(ValueError, match="0, 1"):
        MatchkeyField(field="x", scorer="jaro_winkler",
                      levels=3, level_thresholds=[1.2, 0.9])


def test_default_none_backcompat():
    f = MatchkeyField(field="x", scorer="jaro_winkler", levels=3)
    assert f.level_thresholds is None
    assert f.partial_threshold == 0.8
