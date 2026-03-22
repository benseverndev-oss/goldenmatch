from goldencheck.models.profile import ColumnProfile, DatasetProfile

def test_column_profile_creation():
    cp = ColumnProfile(name="email", inferred_type="string", null_count=50, null_pct=0.1,
                       unique_count=4500, unique_pct=0.9, row_count=5000)
    assert cp.name == "email"
    assert cp.null_pct == 0.1

def test_dataset_profile_creation():
    dp = DatasetProfile(file_path="data.csv", row_count=5000, column_count=10, columns=[])
    assert dp.row_count == 5000

def test_health_score_perfect():
    dp = DatasetProfile(file_path="data.csv", row_count=100, column_count=5, columns=[])
    grade, points = dp.health_score(errors=0, warnings=0)
    assert grade == "A"
    assert points == 100

def test_health_score_with_errors():
    dp = DatasetProfile(file_path="data.csv", row_count=100, column_count=5, columns=[])
    grade, points = dp.health_score(errors=2, warnings=5)
    assert grade == "D"
    assert points == 65

def test_health_score_floor():
    dp = DatasetProfile(file_path="data.csv", row_count=100, column_count=5, columns=[])
    grade, points = dp.health_score(errors=20, warnings=20)
    assert points >= 0
    assert grade == "F"

def test_health_score_per_column_cap():
    dp = DatasetProfile(file_path="data.csv", row_count=100, column_count=5, columns=[])
    # One column with 5 errors = 50 deduction, but capped at 20
    grade, points = dp.health_score(findings_by_column={"bad_col": {"errors": 5, "warnings": 0}})
    assert points == 80  # 100 - 20 (capped)
    assert grade == "B"

def test_health_score_multi_column_cap():
    dp = DatasetProfile(file_path="data.csv", row_count=100, column_count=5, columns=[])
    grade, points = dp.health_score(findings_by_column={
        "col1": {"errors": 5, "warnings": 0},  # 50 -> capped at 20
        "col2": {"errors": 0, "warnings": 3},   # 9 -> not capped
    })
    assert points == 71  # 100 - 20 - 9
    assert grade == "C"
