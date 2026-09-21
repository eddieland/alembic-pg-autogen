from __future__ import annotations

import pytest

from alembic_pg_autogen import (
    ValueSet,
    ValueSetChange,
    classify_value_set_change,
    compare_value_sets,
    parse_value_set,
)

DEPARSED = "(status)::text = ANY ((ARRAY['a'::character varying, 'b'::character varying])::text[])"
"""How ``pg_get_expr`` deparses ``status IN ('a', 'b')`` on a ``varchar`` column."""


class TestParseValueSet:
    @pytest.mark.parametrize(
        "expression",
        [
            "status IN ('a', 'b')",
            "(status IN ('a', 'b'))",
            "status = ANY (ARRAY['a'::text, 'b'::text])",
            "status = ANY (ARRAY['a', 'b'])",
            DEPARSED,
        ],
    )
    def test_both_forms_parse_to_the_same_set(self, expression: str):
        assert parse_value_set(expression) == ValueSet(column="status", values=frozenset({"a", "b"}))

    def test_qualified_column_uses_the_last_segment(self):
        assert parse_value_set("orders.status IN ('a')") == ValueSet(column="status", values=frozenset({"a"}))

    def test_duplicate_values_collapse(self):
        assert parse_value_set("status IN ('a', 'a')") == ValueSet(column="status", values=frozenset({"a"}))

    @pytest.mark.parametrize(
        "expression",
        [
            "status NOT IN ('a', 'b')",
            "status <> ALL (ARRAY['a'::text])",
            "status <> ANY (ARRAY['a'::text])",
            "priority IN (1, 2)",
            "status IN ('a', NULL)",
            "status = 'a'",
            "orders.* IN ('a')",
            "amount > 0",
            "status IN ('a') AND amount > 0",
            "status IN (other_column)",
            "lower(status) IN ('a')",
            "status IN (",
            "1; SELECT 2",
            "*",
            "",
        ],
    )
    def test_other_shapes_are_not_value_sets(self, expression: str):
        assert parse_value_set(expression) is None


class TestCompareValueSets:
    @pytest.mark.parametrize(
        ("current", "desired", "expected"),
        [
            ({"a", "b"}, {"a", "b", "c"}, ValueSetChange.WIDENING),
            ({"a", "b", "c"}, {"a", "b"}, ValueSetChange.NARROWING),
            ({"a", "b"}, {"b", "c"}, ValueSetChange.DISJOINT),
            ({"a"}, {"b"}, ValueSetChange.DISJOINT),
            ({"a", "b"}, {"a", "b"}, ValueSetChange.UNKNOWN),
        ],
    )
    def test_same_column(self, current: set[str], desired: set[str], expected: ValueSetChange):
        result = compare_value_sets(ValueSet("status", frozenset(current)), ValueSet("status", frozenset(desired)))

        assert result is expected

    def test_different_columns_are_unknown(self):
        result = compare_value_sets(ValueSet("status", frozenset({"a"})), ValueSet("kind", frozenset({"a", "b"})))

        assert result is ValueSetChange.UNKNOWN


class TestClassifyValueSetChange:
    def test_mixed_forms_classify(self):
        assert classify_value_set_change(DEPARSED, "status IN ('a', 'b', 'c')") is ValueSetChange.WIDENING

    def test_narrowing_from_deparsed_form(self):
        assert classify_value_set_change(DEPARSED, "status IN ('a')") is ValueSetChange.NARROWING

    @pytest.mark.parametrize(
        ("current", "desired"),
        [
            ("amount > 0", "status IN ('a')"),
            ("status IN ('a')", "amount > 0"),
            ("amount >= 0", "amount > 0"),
        ],
    )
    def test_non_value_sets_are_unknown(self, current: str, desired: str):
        assert classify_value_set_change(current, desired) is ValueSetChange.UNKNOWN
