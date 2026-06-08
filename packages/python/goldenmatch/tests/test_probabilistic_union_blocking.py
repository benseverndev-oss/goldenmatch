from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.pipeline import _collect_blocking_fields


def test_blocking_fields_include_passes():
    blocking = BlockingConfig(
        strategy="multi_pass",
        passes=[
            BlockingKeyConfig(fields=["first_name", "birth_year"]),
            BlockingKeyConfig(fields=["surname"]),
        ],
    )
    assert set(_collect_blocking_fields(blocking)) == {"first_name", "birth_year", "surname"}


def test_blocking_fields_include_keys_only_still_works():
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])
    assert set(_collect_blocking_fields(blocking)) == {"zip"}


def test_blocking_fields_union_of_keys_and_passes_deduped():
    blocking = BlockingConfig(
        strategy="multi_pass",
        keys=[BlockingKeyConfig(fields=["zip"])],
        passes=[BlockingKeyConfig(fields=["zip", "surname"])],
    )
    # union, de-duplicated, order-preserving not required by the assertion
    assert set(_collect_blocking_fields(blocking)) == {"zip", "surname"}


def test_blocking_fields_none_is_empty():
    assert _collect_blocking_fields(None) == []
