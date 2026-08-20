# These tests exercise the comparator's internals directly.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from alembic.operations.ops import ModifyTableOps
from alembic.runtime.plugins import Plugin
from alembic.util import PriorityDispatchResult
from sqlalchemy import CheckConstraint, Column, Enum, Integer, MetaData, Numeric, String, Table, text
from sqlalchemy.dialects import postgresql

from alembic_pg_autogen.compare_check_constraints import (
    _compare_check_constraint_expressions,
    _compile_check_expression,
    _metadata_check_constraints,
    _same_sql,
    setup,
)

from .test_autogenerate import _autogenerate

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect

    from .alembic_helpers import AlembicProject

PG_DIALECT = postgresql.dialect()


def _orders_table(*constraints: CheckConstraint, metadata: MetaData | None = None) -> Table:
    return Table(
        "orders",
        metadata if metadata is not None else MetaData(),
        Column("id", Integer, primary_key=True),
        Column("amount", Numeric()),
        Column("status", String(16)),
        *constraints,
    )


class _StubAutogenContext:
    """Minimal stand-in for ``AutogenContext`` exposing only what the comparator touches."""

    connection: object | None
    dialect: Dialect

    def __init__(self, connection: object | None = None) -> None:
        self.connection = connection
        self.dialect = PG_DIALECT

    def run_name_filters(self, *_args: Any, **_kw: Any) -> bool:
        return True

    def run_object_filters(self, *_args: Any, **_kw: Any) -> bool:
        return True


def _stub_context(connection: object | None = None) -> Any:
    """Return a stub typed as ``Any`` so it can stand in for ``AutogenContext`` without a structural cast."""
    return _StubAutogenContext(connection)


class TestMetadataCheckConstraints:
    def test_named_constraint_collected(self):
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount"))

        constraints = _metadata_check_constraints(table, PG_DIALECT)

        assert set(constraints) == {"ck_orders_amount"}

    def test_unnamed_constraint_skipped(self):
        table = _orders_table(CheckConstraint("amount >= 0"))

        assert _metadata_check_constraints(table, PG_DIALECT) == {}

    def test_naming_convention_resolved(self):
        metadata = MetaData(naming_convention={"ck": "ck_%(table_name)s_%(constraint_name)s"})
        table = _orders_table(CheckConstraint("amount >= 0", name="positive"), metadata=metadata)

        constraints = _metadata_check_constraints(table, PG_DIALECT)

        assert set(constraints) == {"ck_orders_positive"}

    def test_type_bound_constraint_skipped(self):
        table = Table(
            "orders",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("status", Enum("new", "done", name="status_enum", native_enum=False)),
        )

        # The constraint the Enum generates is Alembic's to manage, not ours.
        assert _metadata_check_constraints(table, PG_DIALECT) == {}

    def test_column_level_constraint_collected(self):
        table = Table(
            "orders",
            MetaData(),
            Column("amount", Numeric(), CheckConstraint("amount >= 0", name="ck_orders_amount")),
        )

        assert set(_metadata_check_constraints(table, PG_DIALECT)) == {"ck_orders_amount"}


class TestCompileCheckExpression:
    def test_text_expression(self):
        constraint = CheckConstraint("amount >= 0", name="ck_orders_amount")

        assert _compile_check_expression(constraint, PG_DIALECT) == "amount >= 0"

    def test_sql_expression_renders_literal_binds(self):
        table = _orders_table()
        constraint = CheckConstraint(table.c.amount > 0, name="ck_orders_amount")

        compiled = _compile_check_expression(constraint, PG_DIALECT)

        assert compiled is not None
        assert "amount" in compiled
        assert "> 0" in compiled


class TestSameSql:
    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            ("amount >= 0", "amount >= 0", True),
            ("amount   >=\n0", "amount >= 0", True),
            ("amount >= 0", "amount > 0", False),
            ("amount >= 0", "(amount >= (0)::numeric)", False),
        ],
    )
    def test_whitespace_insensitive_comparison(self, left: str, right: str, expected: bool):
        assert _same_sql(left, right) is expected


class TestComparatorShortCircuits:
    """The comparator must stay out of the way when there is nothing it can compare."""

    def test_new_table_is_alembics_job(self):
        modify_table_ops = ModifyTableOps("orders", [])
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount"))

        result = _compare_check_constraint_expressions(_stub_context(), modify_table_ops, None, "orders", None, table)

        assert result is PriorityDispatchResult.CONTINUE
        assert modify_table_ops.is_empty()

    def test_offline_autogenerate_skipped(self):
        modify_table_ops = ModifyTableOps("orders", [])
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount"))

        result = _compare_check_constraint_expressions(
            _stub_context(connection=None), modify_table_ops, None, "orders", table, table
        )

        assert result is PriorityDispatchResult.CONTINUE
        assert modify_table_ops.is_empty()

    def test_table_without_check_constraints_skipped(self):
        """No metadata constraints means no queries — the stub connection would raise if one were issued."""
        modify_table_ops = ModifyTableOps("orders", [])
        table = _orders_table()

        result = _compare_check_constraint_expressions(
            _stub_context(connection=object()), modify_table_ops, None, "orders", table, table
        )

        assert result is PriorityDispatchResult.CONTINUE
        assert modify_table_ops.is_empty()


class TestSetup:
    def test_registers_table_level_comparator(self):
        plugin = Plugin("test_alembic_pg_autogen_checkconstraints")
        try:
            setup(plugin)
            dispatched = plugin.autogenerate_comparators.dispatch("table", qualifier="postgresql")
        finally:
            plugin.remove()

        assert dispatched is not None


@pytest.mark.integration
class TestCheckConstraintAutogenerateIntegration:
    def test_changed_expression_produces_drop_and_add(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY, amount numeric)")
        alembic_project.execute("ALTER TABLE orders ADD CONSTRAINT ck_orders_amount CHECK (amount >= 0)")

        metadata = MetaData()
        Table(
            "orders",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric()),
            CheckConstraint("amount > 0", name="ck_orders_amount"),
        )

        content = _autogenerate(alembic_project, target_metadata=metadata)

        assert "drop_constraint" in content
        assert "create_check_constraint" in content
        assert "ck_orders_amount" in content

    def test_equivalent_expression_produces_no_constraint_ops(self, alembic_project: AlembicProject):
        """The whole point: ``amount >= 0`` and ``(amount >= (0)::numeric)`` are the same constraint."""
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY, amount numeric)")
        alembic_project.execute("ALTER TABLE orders ADD CONSTRAINT ck_orders_amount CHECK (amount >= 0)")

        metadata = MetaData()
        Table(
            "orders",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric()),
            CheckConstraint("amount >= 0", name="ck_orders_amount"),
        )

        content = _autogenerate(alembic_project, target_metadata=metadata)

        assert "drop_constraint" not in content
        assert "create_check_constraint" not in content

    def test_rewritten_in_list_is_not_a_change(self, alembic_project: AlembicProject):
        """PostgreSQL stores ``IN (...)`` as ``= ANY (ARRAY[...])``; text comparison alone would see a diff."""
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY, status varchar(16))")
        alembic_project.execute("ALTER TABLE orders ADD CONSTRAINT ck_orders_status CHECK (status IN ('new', 'done'))")

        metadata = MetaData()
        Table(
            "orders",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("status", String(16)),
            CheckConstraint("status IN ('new', 'done')", name="ck_orders_status"),
        )

        content = _autogenerate(alembic_project, target_metadata=metadata)

        assert "drop_constraint" not in content

    def test_canonicalization_leaves_no_probe_constraints_behind(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY, amount numeric)")
        alembic_project.execute("ALTER TABLE orders ADD CONSTRAINT ck_orders_amount CHECK (amount >= 0)")

        metadata = MetaData()
        Table(
            "orders",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric()),
            CheckConstraint("amount > 0", name="ck_orders_amount"),
        )

        _autogenerate(alembic_project, target_metadata=metadata)

        with alembic_project.connect() as conn:
            remaining = conn.execute(
                text("SELECT conname FROM pg_catalog.pg_constraint WHERE conname LIKE '\\_alembic\\_pg\\_autogen%'")
            ).all()

        assert remaining == []
