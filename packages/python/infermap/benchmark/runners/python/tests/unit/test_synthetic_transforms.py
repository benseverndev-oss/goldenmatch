"""Tests for the pure transform functions used by the synthetic generator."""
from __future__ import annotations

import random

from infermap_bench.synthetic import (
    Field,
    Schema,
    abbreviate,
    add_distractors,
    case_change,
    column_reorder,
    drop_columns,
    dtype_string_cast,
    prefix_add,
    sample_typos,
    snake_camel_swap,
    suffix_add,
)


def _field(name: str, dtype: str = "string", samples: list[str] | None = None,
           canonical: str | None = None) -> Field:
    """Helper: build a Field with canonical_name defaulting to `name`."""
    return Field(
        name=name,
        dtype=dtype,
        samples=samples or ["a", "b", "c"],
        canonical_name=canonical if canonical is not None else name,
    )


def _schema(*pairs: tuple[str, str]) -> Schema:
    return Schema(name="test", fields=[_field(n, d) for n, d in pairs])


class TestCaseChange:
    def test_changes_field_names(self):
        rng = random.Random(1)
        s = _schema(("first_name", "string"))
        out = case_change(s, rng)
        assert out.fields[0].name in {"firstName", "FirstName", "FIRST_NAME"}

    def test_preserves_canonical_name(self):
        rng = random.Random(1)
        s = _schema(("first_name", "string"))
        out = case_change(s, rng)
        assert out.fields[0].canonical_name == "first_name"


class TestSnakeCamelSwap:
    def test_snake_to_camel(self):
        rng = random.Random(1)
        s = _schema(("first_name", "string"))
        out = snake_camel_swap(s, rng)
        assert out.fields[0].name == "firstName"
        assert out.fields[0].canonical_name == "first_name"

    def test_camel_to_snake(self):
        rng = random.Random(1)
        s = Schema(name="t", fields=[_field("firstName")])
        out = snake_camel_swap(s, rng)
        assert out.fields[0].name == "first_name"
        assert out.fields[0].canonical_name == "firstName"


class TestAbbreviate:
    def test_replaces_matched_names(self):
        rng = random.Random(1)
        s = _schema(("first_name", "string"), ("email_address", "string"))
        table = {"first_name": "fname", "email_address": "email_addr"}
        out = abbreviate(s, rng, table=table)
        assert out.fields[0].name == "fname"
        assert out.fields[1].name == "email_addr"

    def test_leaves_unmatched_unchanged(self):
        rng = random.Random(1)
        s = _schema(("something_else", "string"))
        out = abbreviate(s, rng, table={"first_name": "fname"})
        assert out.fields[0].name == "something_else"

    def test_preserves_canonical_name(self):
        rng = random.Random(1)
        s = _schema(("first_name", "string"))
        out = abbreviate(s, rng, table={"first_name": "fname"})
        assert out.fields[0].canonical_name == "first_name"


class TestPrefixAdd:
    def test_prepends_prefix(self):
        rng = random.Random(1)
        s = _schema(("name", "string"))
        out = prefix_add(s, rng, choices=["cust"])
        assert out.fields[0].name == "cust_name"

    def test_preserves_canonical(self):
        rng = random.Random(1)
        s = _schema(("name", "string"))
        out = prefix_add(s, rng, choices=["cust"])
        assert out.fields[0].canonical_name == "name"

    def test_single_prefix_applied_to_all_fields(self):
        rng = random.Random(42)
        s = _schema(("a", "string"), ("b", "string"), ("c", "string"))
        out = prefix_add(s, rng, choices=["sys"])
        assert [f.name for f in out.fields] == ["sys_a", "sys_b", "sys_c"]


class TestSuffixAdd:
    def test_appends_suffix(self):
        rng = random.Random(1)
        s = _schema(("name", "string"))
        out = suffix_add(s, rng, choices=["v2"])
        assert out.fields[0].name == "name_v2"

    def test_preserves_canonical(self):
        rng = random.Random(1)
        s = _schema(("name", "string"))
        out = suffix_add(s, rng, choices=["v2"])
        assert out.fields[0].canonical_name == "name"


class TestColumnReorder:
    def test_permutes_fields(self):
        rng = random.Random(1)
        s = _schema(("a", "string"), ("b", "string"), ("c", "string"), ("d", "string"))
        out = column_reorder(s, rng)
        assert {f.name for f in out.fields} == {"a", "b", "c", "d"}

    def test_preserves_canonical_names(self):
        rng = random.Random(1)
        s = _schema(("a", "string"), ("b", "string"))
        out = column_reorder(s, rng)
        for f in out.fields:
            assert f.canonical_name == f.name


class TestDropColumns:
    def test_drops_count_fields(self):
        rng = random.Random(1)
        s = _schema(("a", "string"), ("b", "string"), ("c", "string"), ("d", "string"))
        out, dropped = drop_columns(s, rng, count=1)
        assert len(out.fields) == 3
        assert len(dropped) == 1

    def test_dropped_returns_canonical_names(self):
        rng = random.Random(1)
        s = Schema(name="t", fields=[
            _field("renamed_a", canonical="a"),
            _field("renamed_b", canonical="b"),
            _field("renamed_c", canonical="c"),
        ])
        out, dropped = drop_columns(s, rng, count=1)
        assert dropped[0] in {"a", "b", "c"}
        assert dropped[0] not in {"renamed_a", "renamed_b", "renamed_c"}

    def test_too_few_fields_returns_unchanged(self):
        rng = random.Random(1)
        s = _schema(("only", "string"))
        out, dropped = drop_columns(s, rng, count=5)
        assert out == s
        assert dropped == []


class TestAddDistractors:
    def test_appends_distractors(self):
        rng = random.Random(1)
        s = _schema(("a", "string"))
        pool = [Field(name="distract_1", dtype="string", samples=["x"], canonical_name=None)]
        out, added = add_distractors(s, rng, pool=pool, count=1)
        assert len(out.fields) == 2
        assert "distract_1" in added

    def test_distractors_have_none_canonical(self):
        rng = random.Random(1)
        s = _schema(("a", "string"))
        pool = [Field(name="distract_1", dtype="string", samples=["x"], canonical_name=None)]
        out, _ = add_distractors(s, rng, pool=pool, count=1)
        for f in out.fields:
            if f.name == "distract_1":
                assert f.canonical_name is None

    def test_empty_pool_no_op(self):
        rng = random.Random(1)
        s = _schema(("a", "string"))
        out, added = add_distractors(s, rng, pool=[], count=3)
        assert out == s
        assert added == []

    def test_count_zero_no_op(self):
        rng = random.Random(1)
        s = _schema(("a", "string"))
        pool = [Field(name="d", dtype="string", samples=["x"], canonical_name=None)]
        out, added = add_distractors(s, rng, pool=pool, count=0)
        assert out == s
        assert added == []


class TestDtypeStringCast:
    def test_converts_non_strings(self):
        rng = random.Random(1)
        s = Schema(name="t", fields=[
            _field("age", dtype="integer"),
            _field("name", dtype="string"),
            _field("created", dtype="date"),
        ])
        out = dtype_string_cast(s, rng)
        assert all(f.dtype == "string" for f in out.fields)

    def test_preserves_canonical_names(self):
        rng = random.Random(1)
        s = Schema(name="t", fields=[_field("age", dtype="integer")])
        out = dtype_string_cast(s, rng)
        assert out.fields[0].canonical_name == "age"


class TestSampleTypos:
    def test_mutates_samples(self):
        rng = random.Random(1)
        f = _field("x", samples=["hello", "world", "foobar", "bazqux", "spam"])
        s = Schema(name="t", fields=[f])
        out = sample_typos(s, rng, rate=1.0)
        assert out.fields[0].samples != f.samples

    def test_preserves_canonical_name(self):
        rng = random.Random(1)
        f = _field("x", samples=["hello", "world"], canonical="x")
        s = Schema(name="t", fields=[f])
        out = sample_typos(s, rng, rate=1.0)
        assert out.fields[0].canonical_name == "x"

    def test_preserves_field_name(self):
        rng = random.Random(1)
        f = _field("email", samples=["abc", "def", "ghi"])
        s = Schema(name="t", fields=[f])
        out = sample_typos(s, rng, rate=1.0)
        assert out.fields[0].name == "email"

    def test_short_samples_untouched(self):
        rng = random.Random(1)
        f = _field("x", samples=["a", "b", ""])
        s = Schema(name="t", fields=[f])
        out = sample_typos(s, rng, rate=1.0)
        assert out.fields[0].samples == ["a", "b", ""]


class TestDeterminism:
    def test_same_seed_produces_same_output(self):
        s = _schema(("first_name", "string"), ("last_name", "string"))
        rng_a = random.Random(42)
        rng_b = random.Random(42)
        out_a = case_change(s, rng_a)
        out_b = case_change(s, rng_b)
        assert [f.name for f in out_a.fields] == [f.name for f in out_b.fields]
