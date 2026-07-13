import pytest
from goldenmatch.config.from_splink import RecognizedLevel, recognize_level


@pytest.mark.parametrize(
    "sql",
    [
        '"first_name_l" IS NULL OR "first_name_r" IS NULL',
        "first_name_l IS NULL OR first_name_r IS NULL",
        '"first_name_l"   is   null   or   "first_name_r"   is   null',
    ],
)
def test_null_level(sql):
    result = recognize_level(sql)
    assert result == RecognizedLevel("null", "first_name", None)


def test_is_null_level_flag_forces_null_regardless_of_sql():
    result = recognize_level('"amount_l" > "amount_r"', is_null_level=True)
    assert result is not None
    assert result.kind == "null"


@pytest.mark.parametrize(
    "sql",
    [
        '"first_name_l" = "first_name_r"',
        "first_name_l = first_name_r",
        '"first_name_l"    =    "first_name_r"',
    ],
)
def test_exact_level(sql):
    result = recognize_level(sql)
    assert result == RecognizedLevel("exact", "first_name", 1.0)


@pytest.mark.parametrize(
    "sql",
    [
        'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92',
        "JARO_WINKLER_SIMILARITY(first_name_l, first_name_r) >= 0.92",
        '  jaro_winkler_similarity(  "first_name_l" ,  "first_name_r"  )   >=   0.92  ',
    ],
)
def test_jaro_winkler_similarity_level(sql):
    result = recognize_level(sql)
    assert result == RecognizedLevel("jaro_winkler", "first_name", 0.92, approx=False)


def test_leading_dot_float_threshold():
    result = recognize_level('jaro_winkler_similarity("first_name_l", "first_name_r") >= .92')
    assert result == RecognizedLevel("jaro_winkler", "first_name", 0.92, approx=False)


def test_strict_greater_than_returns_none():
    result = recognize_level('jaro_winkler_similarity("first_name_l", "first_name_r") > 0.92')
    assert result is None


@pytest.mark.parametrize(
    "sql",
    [
        'jaro_winkler("a_l","a_r") >= 0.9',
        "JARO_WINKLER(a_l, a_r) >= 0.9",
    ],
)
def test_jaro_winkler_spark_dialect_level(sql):
    result = recognize_level(sql)
    assert result == RecognizedLevel("jaro_winkler", "a", 0.9, approx=False)


def test_jaro_similarity_is_approximated_as_jaro_winkler():
    result = recognize_level('jaro_similarity("x_l", "x_r") >= 0.9')
    assert result == RecognizedLevel("jaro_winkler", "x", 0.9, approx=True)


def test_levenshtein_level():
    result = recognize_level('levenshtein("dob_l", "dob_r") <= 1')
    assert result is not None
    assert result.kind == "levenshtein"
    assert result.column == "dob"
    assert result.sim_threshold == pytest.approx(1 - 1 / 10)
    assert result.approx is True


def test_damerau_levenshtein_level():
    result = recognize_level('damerau_levenshtein("dob_l", "dob_r") <= 2')
    assert result is not None
    assert result.kind == "levenshtein"
    assert result.sim_threshold == pytest.approx(1 - 2 / 10)
    assert result.approx is True


def test_jaccard_level():
    result = recognize_level('jaccard("email_l", "email_r") >= 0.9')
    assert result == RecognizedLevel("jaccard", "email", 0.9, approx=False)


def test_else_level():
    result = recognize_level("ELSE")
    assert result == RecognizedLevel("else", None, None)

    result_lower = recognize_level("else")
    assert result_lower == RecognizedLevel("else", None, None)


def test_cross_column_returns_none():
    result = recognize_level('"first_name_l" = "surname_r" AND "surname_l" = "first_name_r"')
    assert result is None


def test_mismatched_columns_in_function_returns_none():
    result = recognize_level('jaro_winkler_similarity("a_l", "b_r") >= 0.9')
    assert result is None


def test_arbitrary_sql_returns_none():
    result = recognize_level('abs("amount_l" - "amount_r") < 5')
    assert result is None


def test_levenshtein_distance_floor_clamps_to_zero():
    result = recognize_level('levenshtein("dob_l", "dob_r") <= 15')
    assert result is not None
    assert result.kind == "levenshtein"
    assert result.sim_threshold == 0.0
    assert result.approx is True
