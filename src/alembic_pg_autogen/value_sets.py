"""Classification of check constraint changes that widen or narrow a set of allowed values.

A non-native SQLAlchemy ``Enum`` compiles to ``status IN ('a', 'b')``.  PostgreSQL stores that constraint as
``status = ANY (ARRAY['a'::text, 'b'::text])``, with casts that depend on the column type.  This module parses both
forms with postgast, reduces each to a column name and a set of string values, and reports how the set changed.  The
comparator uses the report to decide whether a change can be added ``NOT VALID`` and whether the migration needs a
backfill note.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterable

    from postgast.pg_query_pb2 import Node


class ValueSet(NamedTuple):
    """One column restricted to a set of string literals, as parsed from a check expression."""

    column: str
    values: frozenset[str]


class ValueSetChange(enum.Enum):
    """How the set of allowed values changed between two check expressions."""

    WIDENING = "widening"
    """The desired set holds every current value and at least one more."""

    NARROWING = "narrowing"
    """The current set holds every desired value and at least one more."""

    DISJOINT = "disjoint"
    """The desired set adds values and removes values.  Two sets with no value in common are included."""

    UNKNOWN = "unknown"
    """Either expression is not a value set, the columns differ, or the sets are equal."""


def classify_value_set_change(current: str, desired: str) -> ValueSetChange:
    """Classify the change from the *current* check expression to the *desired* one.

    Both expressions may use the ``IN`` form or the ``= ANY (ARRAY[...])`` form.  The result is
    :attr:`ValueSetChange.UNKNOWN` when either expression is not a value set.
    """
    current_set = parse_value_set(current)
    desired_set = parse_value_set(desired)
    if current_set is None or desired_set is None:
        return ValueSetChange.UNKNOWN
    return compare_value_sets(current_set, desired_set)


def compare_value_sets(current: ValueSet, desired: ValueSet) -> ValueSetChange:
    """Classify the change between two parsed value sets on the same column."""
    if current.column != desired.column or current.values == desired.values:
        return ValueSetChange.UNKNOWN
    if current.values < desired.values:
        return ValueSetChange.WIDENING
    if desired.values < current.values:
        return ValueSetChange.NARROWING
    return ValueSetChange.DISJOINT


def parse_value_set(expression: str) -> ValueSet | None:
    """Parse ``col IN ('a', ...)`` or ``col = ANY (ARRAY['a', ...])`` into a :class:`ValueSet`.

    Casts around the column, the array, and each element are ignored, because ``pg_get_expr`` adds them.  A qualified
    column such as ``orders.status`` yields ``status``.  Returns *None* for a negated form, a non-string literal, any
    other expression shape, and text that PostgreSQL cannot parse.
    """
    import postgast
    from postgast.pg_query_pb2 import A_Expr_Kind

    try:
        tree = postgast.parse(f"SELECT {expression}")
    except postgast.PgQueryError:
        return None
    if len(tree.stmts) != 1:
        return None
    statement = tree.stmts[0].stmt
    if statement.WhichOneof("node") != "select_stmt" or len(statement.select_stmt.target_list) != 1:
        return None
    target = statement.select_stmt.target_list[0].res_target.val
    if target.WhichOneof("node") != "a_expr":
        return None

    expr = target.a_expr
    if len(expr.name) != 1 or expr.name[0].WhichOneof("node") != "string" or expr.name[0].string.sval != "=":
        return None
    column = _column_name(_strip_casts(expr.lexpr))
    if column is None:
        return None

    rexpr = _strip_casts(expr.rexpr)
    items: Iterable[Node]
    if expr.kind == A_Expr_Kind.AEXPR_IN and rexpr.WhichOneof("node") == "list":
        items = rexpr.list.items
    elif expr.kind == A_Expr_Kind.AEXPR_OP_ANY and rexpr.WhichOneof("node") == "a_array_expr":
        items = rexpr.a_array_expr.elements
    else:
        return None

    values: set[str] = set()
    for item in items:
        value = _string_constant(item)
        if value is None:
            return None
        values.add(value)
    return ValueSet(column=column, values=frozenset(values))


def _strip_casts(node: Node) -> Node:
    """Return the innermost node under any chain of ``TypeCast`` wrappers."""
    while node.WhichOneof("node") == "type_cast":
        node = node.type_cast.arg
    return node


def _column_name(node: Node) -> str | None:
    """Return the unqualified column name of a ``ColumnRef`` node, or *None* for any other node."""
    if node.WhichOneof("node") != "column_ref" or not node.column_ref.fields:
        return None
    last = node.column_ref.fields[-1]
    if last.WhichOneof("node") != "string":
        return None
    return last.string.sval


def _string_constant(node: Node) -> str | None:
    """Return the value of a string constant, looking through casts, or *None* for any other node."""
    node = _strip_casts(node)
    if node.WhichOneof("node") != "a_const" or node.a_const.WhichOneof("val") != "sval":
        return None
    return node.a_const.sval.sval
