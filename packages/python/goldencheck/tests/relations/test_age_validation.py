import datetime

import polars as pl

from goldencheck.relations.age_validation import AgeValidationProfiler, _is_age_column, _is_dob_column
from goldencheck.models.finding import Severity


def test_is_age_column_basic():
    assert _is_age_column("age") is True
    assert _is_age_column("Age") is True
    assert _is_age_column("patient_age") is True


def test_is_age_column_exclusions():
    assert _is_age_column("stage") is False
    assert _is_age_column("page") is False
    assert _is_age_column("usage") is False
    assert _is_age_column("mileage") is False
    assert _is_age_column("dosage") is False
    assert _is_age_column("voltage") is False


def test_is_age_column_no_match():
    assert _is_age_column("name") is False
    assert _is_age_column("score") is False


def test_is_dob_column():
    assert _is_dob_column("dob") is True
    assert _is_dob_column("date_of_birth") is True
    assert _is_dob_column("DOB") is True
    assert _is_dob_column("born_date") is True
    assert _is_dob_column("name") is False


def test_no_age_or_dob_columns():
    df = pl.DataFrame({"name": ["Alice"], "score": [100]})
    findings = AgeValidationProfiler().profile(df)
    assert findings == []


def test_age_only_no_dob():
    df = pl.DataFrame({"age": [30], "name": ["Alice"]})
    findings = AgeValidationProfiler().profile(df)
    assert findings == []


def test_dob_only_no_age():
    df = pl.DataFrame({"dob": ["1990-01-01"], "name": ["Alice"]})
    findings = AgeValidationProfiler().profile(df)
    assert findings == []


def test_matching_age_and_dob():
    """Age consistent with DOB should produce no findings."""
    today = datetime.date.today()
    # Person born 30 years ago (approximately)
    birth = today.replace(year=today.year - 30)
    df = pl.DataFrame({
        "age": [30],
        "dob": [birth.isoformat()],
    })
    findings = AgeValidationProfiler().profile(df)
    assert len(findings) == 0


def test_mismatching_age_and_dob():
    """Age very different from DOB should produce ERROR."""
    today = datetime.date.today()
    birth = today.replace(year=today.year - 30)
    df = pl.DataFrame({
        "age": [50],  # claimed 50 but born 30 years ago
        "dob": [birth.isoformat()],
    })
    findings = AgeValidationProfiler().profile(df)
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].check == "cross_column"
    assert findings[0].affected_rows == 1


def test_non_numeric_age_column_skipped():
    """Non-numeric age column should be skipped."""
    df = pl.DataFrame({
        "age": ["thirty", "forty"],
        "dob": ["1994-01-01", "1984-01-01"],
    })
    findings = AgeValidationProfiler().profile(df)
    assert findings == []


def test_multiple_mismatches():
    today = datetime.date.today()
    birth1 = today.replace(year=today.year - 25)
    birth2 = today.replace(year=today.year - 35)
    df = pl.DataFrame({
        "age": [60, 60],  # both wrong
        "dob": [birth1.isoformat(), birth2.isoformat()],
    })
    findings = AgeValidationProfiler().profile(df)
    assert len(findings) == 1
    assert findings[0].affected_rows == 2
