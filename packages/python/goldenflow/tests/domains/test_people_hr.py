import polars as pl

from goldenflow.domains.people_hr import PACK, ssn_mask, ssn_validate


def test_pack_metadata():
    assert PACK.name == "people_hr"
    assert len(PACK.transforms) > 0


def test_ssn_mask():
    s = pl.Series("ssn", ["123-45-6789", "987-65-4321", "invalid"])
    result = ssn_mask(s)
    assert result[0] == "***-**-6789"
    assert result[1] == "***-**-4321"
    assert result[2] == "invalid"


def test_ssn_validate():
    s = pl.Series("ssn", ["123-45-6789", "000-00-0000", "invalid", "123456789"])
    result = ssn_validate(s)
    assert result[0] is True
    assert result[1] is False  # all zeros invalid
    assert result[2] is False
    assert result[3] is True  # digits-only valid
