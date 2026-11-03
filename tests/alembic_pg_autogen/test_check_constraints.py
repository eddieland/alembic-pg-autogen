# These tests exercise the comparator's internals directly.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import enum
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic.command import downgrade, revision, upgrade
from alembic.operations.ops import CreateCheckConstraintOp, DropConstraintOp, ModifyTableOps
from alembic.runtime.plugins import Plugin
from alembic.util import DispatchPriority, PriorityDispatchResult
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Enum,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    bindparam,
    exc,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql.base import PGDialect

from alembic_pg_autogen import (
    CheckConstraintInfo,
    CreateCheckConstraintNotValidOp,
    CreateCheckConstraintTextOp,
    DropCheckConstraintOp,
    ValidateConstraintOp,
)
from alembic_pg_autogen import compare_check_constraints as module
from alembic_pg_autogen.compare_check_constraints import (
    VALIDATION_MODE_KEY,
    _compare_check_constraint_expressions,
    _compile_check_expression,
    _metadata_check_constraints,
    _same_sql,
    _unnamed_check_constraints,
    _validation_mode,
    setup,
)
from alembic_pg_autogen.inspect import inspect_check_constraints

from .test_autogenerate import _autogenerate

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from typing import Final

    from sqlalchemy.engine import Dialect
    from sqlalchemy.sql.elements import ColumnElement

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


def _always_created(_compiler: object) -> bool:
    """A ``_create_rule`` that accepts every dialect, standing in for the rule SQLAlchemy attaches to an ``Enum``."""
    return True


def _jsonb_comparison() -> ColumnElement[bool]:
    """Return an expression PostgreSQL can run but SQLAlchemy cannot render with literal binds."""
    table = Table("payloads", MetaData(), Column("payload", JSONB()))
    return table.c.payload == bindparam("value", value={"a": 1}, type_=JSONB())


class _StubAutogenContext:
    """Minimal stand-in for ``AutogenContext`` exposing only what the comparator touches."""

    connection: object | None
    dialect: Dialect
    opts: dict[str, Any]
    name_filter_result: bool | Callable[[str], bool]
    object_filter_result: bool

    def __init__(self, connection: object | None = None, opts: dict[str, Any] | None = None) -> None:
        self.connection = connection
        self.dialect = PG_DIALECT
        self.opts = opts if opts is not None else {}
        self.name_filter_result = True
        self.object_filter_result = True

    def run_name_filters(self, name: str, *_args: Any, **_kw: Any) -> bool:
        if callable(self.name_filter_result):
            return self.name_filter_result(name)
        return self.name_filter_result

    def run_object_filters(self, *_args: Any, **_kw: Any) -> bool:
        return self.object_filter_result


def _stub_context(connection: object | None = None, opts: dict[str, Any] | None = None) -> Any:
    """Return a stub typed as ``Any`` so it can stand in for ``AutogenContext`` without a structural cast."""
    return _StubAutogenContext(connection, opts)


class Status(enum.Enum):
    new = "new"
    done = "done"


class WiderStatus(enum.Enum):
    new = "new"
    done = "done"
    shipped = "shipped"


def _enum_orders_table(status: type[enum.Enum], *, native_enum: bool = False) -> Table:
    """An ``orders`` table whose ``status`` column is a SQLAlchemy ``Enum`` named for its check constraint."""
    return Table(
        "orders",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column(
            "status", Enum(status, native_enum=native_enum, create_constraint=True, name="ck_orders_status", length=16)
        ),
    )


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

    def test_non_native_enum_constraint_collected(self):
        """PostgreSQL holds the constraint a non-native ``Enum`` generates, so it is ours to compare."""
        table = _enum_orders_table(Status)

        constraints = _metadata_check_constraints(table, PG_DIALECT)

        assert set(constraints) == {"ck_orders_status"}
        assert getattr(constraints["ck_orders_status"], "_type_bound", False) is True

    def test_native_enum_constraint_skipped(self):
        """A native ``Enum`` becomes a ``pg_enum`` type on PostgreSQL; SQLAlchemy never creates its constraint."""
        table = _enum_orders_table(Status, native_enum=True)

        assert _metadata_check_constraints(table, PG_DIALECT) == {}

    def test_boolean_constraint_skipped(self):
        """PostgreSQL has a native boolean type, so SQLAlchemy never creates the ``IN (0, 1)`` constraint."""
        table = Table(
            "orders",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("flag", Boolean(create_constraint=True, name="ck_orders_flag")),
        )

        assert _metadata_check_constraints(table, PG_DIALECT) == {}

    def test_type_bound_constraint_without_a_create_rule_skipped(self):
        """SQLAlchemy always attaches a rule; a constraint that lacks one keeps the old skip behavior."""
        constraint = CheckConstraint("amount >= 0", name="ck_orders_amount", _type_bound=True)
        table = _orders_table(constraint)

        assert _metadata_check_constraints(table, PG_DIALECT) == {}

    def test_enum_constraint_honors_naming_convention(self):
        metadata = MetaData(naming_convention={"ck": "ck_%(table_name)s_%(constraint_name)s"})
        table = Table(
            "orders",
            metadata,
            Column("status", Enum(Status, native_enum=False, create_constraint=True, name="status")),
        )

        assert set(_metadata_check_constraints(table, PG_DIALECT)) == {"ck_orders_status"}

    def test_blank_name_skipped(self):
        """A name that compiles to the empty string cannot be matched against the catalog, so it is not ours."""
        table = _orders_table(CheckConstraint("amount >= 0", name=""))

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

        assert compiled == "amount > 0"

    def test_column_expression_omits_the_table_prefix(self):
        """SQLAlchemy's DDL compiler renders ``status IN (...)``, not ``orders.status IN (...)``; so do we."""
        table = _orders_table()
        constraint = CheckConstraint(table.c.status.in_(["a", "b"]), name="ck_orders_status")

        assert _compile_check_expression(constraint, PG_DIALECT) == "status IN ('a', 'b')"

    def test_enum_constraint_compiles_to_the_in_form(self):
        (constraint,) = _metadata_check_constraints(_enum_orders_table(Status), PG_DIALECT).values()

        assert _compile_check_expression(constraint, PG_DIALECT) == "status IN ('new', 'done')"


class TestSameSql:
    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            ("amount >= 0", "amount >= 0", True),
            ("amount   >=\n0", "amount >= 0", True),
            ("amount >= 0", "amount > 0", False),
            ("amount >= 0", "(amount >= (0)::numeric)", False),
            ("code = 'x  y'", "code = 'x y'", False),
            ("code  =  'x  y'", "code = 'x  y'", True),
            ("code = 'it''s  x'", "code =  'it''s  x'", True),
            ('"A  B" > 0', '"A B" > 0', False),
            ("code = E'a\\' b'", "code = E'a\\'  b'", False),
        ],
    )
    def test_whitespace_insensitive_comparison(self, left: str, right: str, expected: bool):
        assert _same_sql(left, right) is expected


class TestCompileCheckExpressionPercent:
    """A ``pyformat`` dialect doubles ``%`` in compiled SQL; the comparator works with SQL text, so it undoes that."""

    @pytest.mark.parametrize("dialect", [PG_DIALECT, PGDialect(paramstyle="named")], ids=["pyformat", "named"])
    def test_percent_compiles_once(self, dialect: Dialect):
        constraint = CheckConstraint("code LIKE 'A%' AND q % 2 = 0")

        assert _compile_check_expression(constraint, dialect) == "code LIKE 'A%' AND q % 2 = 0"

    def test_enum_value_with_a_percent_compiles_once(self):
        table = Table("o", MetaData(), Column("code", Enum("10%", native_enum=False, create_constraint=True)))
        (constraint,) = _unnamed_check_constraints(table, PG_DIALECT)

        assert _compile_check_expression(constraint, PG_DIALECT) == "code IN ('10%')"


class TestCompileCheckExpressionFailure:
    def test_expression_without_a_literal_renderer_returns_none(self):
        constraint = CheckConstraint(_jsonb_comparison(), name="ck_orders_payload")

        assert _compile_check_expression(constraint, PG_DIALECT) is None


class TestComparatorSkips:
    """Everything the comparator declines to act on, short of emitting operations."""

    @pytest.fixture
    def catalog(self, monkeypatch: pytest.MonkeyPatch):
        """Stub the catalog and canonicalization calls so the comparator can run without a database."""

        def install(current: dict[str, str], normalized: dict[str, str], *, not_valid: Iterable[str] = ()) -> list[str]:
            canonicalized: list[str] = []
            unvalidated = set(not_valid)

            def fake_inspect(_conn: object, **_kw: Any) -> list[CheckConstraintInfo]:
                return [
                    CheckConstraintInfo("public", "orders", name, expression, validated=name not in unvalidated)
                    for name, expression in current.items()
                ]

            def fake_canonicalize(_conn: object, **kwargs: Any) -> dict[str, str]:
                canonicalized.extend(kwargs["expressions"])
                return normalized

            monkeypatch.setattr(module, "inspect_check_constraints", fake_inspect)
            monkeypatch.setattr(module, "canonicalize_check_constraints", fake_canonicalize)
            return canonicalized

        return install

    def _run(
        self,
        table: Table,
        context: Any = None,
        *,
        existing: list[Any] | None = None,
        opts: dict[str, Any] | None = None,
        conn_table: Table | None = None,
    ) -> ModifyTableOps:
        modify_table_ops = ModifyTableOps("orders", existing if existing is not None else [])
        result = _compare_check_constraint_expressions(
            context if context is not None else _stub_context(connection=object(), opts=opts),
            modify_table_ops,
            "public",
            "orders",
            conn_table if conn_table is not None else table,
            table,
        )
        assert result is PriorityDispatchResult.CONTINUE
        return modify_table_ops

    def test_constraint_missing_from_the_catalog_is_alembics_job(self, catalog: Any):
        probed = catalog({"ck_other": "other > 0"}, {})
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount"))

        assert self._run(table).is_empty()
        assert probed == []

    def test_text_already_matching_the_catalog_skips_the_round_trip(self, catalog: Any):
        probed = catalog({"ck_orders_amount": "amount  >  0"}, {})
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount"))

        assert self._run(table).is_empty()
        assert probed == []

    def test_uncompilable_expression_is_treated_as_unchanged(self, catalog: Any):
        probed = catalog({"ck_orders_amount": "amount > 0::numeric"}, {})
        table = _orders_table(CheckConstraint(_jsonb_comparison(), name="ck_orders_amount"))

        assert self._run(table).is_empty()
        assert probed == []

    def test_expression_that_could_not_be_canonicalized_is_treated_as_unchanged(self, catalog: Any):
        probed = catalog({"ck_orders_amount": "amount > 0::numeric"}, {})
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount"))

        assert self._run(table).is_empty()
        assert probed == ["ck_orders_amount"]

    def test_object_filter_can_veto_the_change(self, catalog: Any):
        catalog({"ck_orders_amount": "amount > 0::numeric"}, {"ck_orders_amount": "amount >= 0::numeric"})
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount"))

        context = _stub_context(connection=object())
        context.object_filter_result = False

        assert self._run(table, context).is_empty()

    def test_name_filter_can_veto_the_change(self, catalog: Any):
        probed = catalog({"ck_orders_amount": "amount > 0::numeric"}, {})
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount"))

        context = _stub_context(connection=object())
        context.name_filter_result = False

        assert self._run(table, context).is_empty()
        assert probed == []

    def test_differing_expression_emits_drop_then_add(self, catalog: Any):
        catalog({"ck_orders_amount": "amount > 0::numeric"}, {"ck_orders_amount": "amount >= 0::numeric"})
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount"))

        ops = self._run(table).ops

        assert [type(op).__name__ for op in ops] == ["DropCheckConstraintOp", "CreateCheckConstraintTextOp"]


class TestTypeBoundOwnership(TestComparatorSkips):
    """Alembic's plugin ignores type-bound constraints, so their existence is this comparator's job."""

    def test_alembics_drop_of_a_declared_enum_constraint_is_discarded(self, catalog: Any):
        catalog({"ck_orders_status": CURRENT_STATUS}, {"ck_orders_status": CURRENT_STATUS})
        spurious = DropConstraintOp("ck_orders_status", "orders", type_="check")

        assert self._run(_enum_orders_table(Status), existing=[spurious]).is_empty()

    def test_drop_of_a_constraint_the_model_no_longer_declares_is_kept(self, catalog: Any):
        """``create_constraint=False`` removes the constraint from the model; Alembic's drop is then correct."""
        catalog({"ck_orders_status": CURRENT_STATUS, "ck_orders_amount": "amount > 0::numeric"}, {})
        drop = DropConstraintOp("ck_orders_status", "orders", type_="check")
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount"))

        assert self._run(table, existing=[drop]).ops == [drop]

    def test_drop_of_a_plain_constraint_is_kept(self, catalog: Any):
        catalog(
            {"ck_orders_status": CURRENT_STATUS, "ck_orders_amount": "amount > 0::numeric"},
            {},
        )
        drop = DropConstraintOp("ck_orders_amount", "orders", type_="check")
        table = _enum_orders_table(Status)

        ops = self._run(table, existing=[drop]).ops

        assert drop in ops

    def test_discarded_drop_still_lets_a_changed_expression_through(self, catalog: Any):
        catalog({"ck_orders_status": WIDER_STATUS}, {"ck_orders_status": CURRENT_STATUS})
        spurious = DropConstraintOp("ck_orders_status", "orders", type_="check")

        ops = self._run(_enum_orders_table(Status), existing=[spurious]).ops

        assert [type(op) for op in ops] == [DropCheckConstraintOp, CreateCheckConstraintNotValidOp]
        assert ops[0] is not spurious

    def test_missing_enum_constraint_is_added(self, catalog: Any):
        probed = catalog({}, {})

        (op,) = self._run(_enum_orders_table(Status)).ops

        assert type(op) is CreateCheckConstraintTextOp
        assert op.constraint_name == "ck_orders_status"
        assert probed == []

    def test_alembics_add_of_a_missing_enum_constraint_is_replaced(self, catalog: Any):
        """Alembic 1.20 adds the missing constraint too; a second add of the same name fails the upgrade."""
        catalog({}, {})
        alembics = CreateCheckConstraintOp("ck_orders_status", "orders", "status IN ('new', 'done')")

        (op,) = self._run(_enum_orders_table(Status), existing=[alembics]).ops

        assert type(op) is CreateCheckConstraintTextOp
        assert op.constraint_name == "ck_orders_status"

    def test_alembics_add_of_another_constraint_is_kept(self, catalog: Any):
        catalog({}, {})
        other = CreateCheckConstraintOp("ck_orders_amount", "orders", "amount > 0")

        ops = self._run(_enum_orders_table(Status), existing=[other]).ops

        assert ops[0] is other
        assert [type(op) for op in ops] == [CreateCheckConstraintOp, CreateCheckConstraintTextOp]

    def test_alembics_add_stays_when_a_filter_skips_the_constraint(self, catalog: Any):
        catalog({}, {})
        alembics = CreateCheckConstraintOp("ck_orders_status", "orders", "status IN ('new', 'done')")
        context = _stub_context(connection=object())
        context.object_filter_result = False

        assert self._run(_enum_orders_table(Status), context, existing=[alembics]).ops == [alembics]

    def test_alembics_add_for_a_column_alembic_is_adding_is_discarded(self, catalog: Any):
        """``op.add_column()`` creates the constraint, so Alembic's extra add would fail the upgrade."""
        catalog({}, {})
        alembics = CreateCheckConstraintOp("ck_orders_status", "orders", "status IN ('new', 'done')")
        reflected = Table("orders", MetaData(), Column("id", Integer, primary_key=True))

        assert self._run(_enum_orders_table(Status), conn_table=reflected, existing=[alembics]).is_empty()

    def test_constraint_of_a_column_alembic_is_adding_is_left_to_add_column(self, catalog: Any):
        """``op.add_column()`` creates the constraint with the column; a second add would fail the upgrade."""
        catalog({}, {})
        reflected = Table("orders", MetaData(), Column("id", Integer, primary_key=True))

        assert self._run(_enum_orders_table(Status), conn_table=reflected).is_empty()

    def test_missing_constraint_declaring_not_valid_is_added_not_valid(self, catalog: Any):
        """Alembic's renderer drops ``postgresql_not_valid``; the subclass keeps the declared state in the migration."""
        catalog({}, {})
        constraint = CheckConstraint(
            "amount >= 0",
            name="ck_orders_amount",
            _type_bound=True,
            _create_rule=_always_created,
            postgresql_not_valid=True,
        )

        (op,) = self._run(_orders_table(constraint)).ops

        assert isinstance(op, CreateCheckConstraintNotValidOp)
        assert op.constraint_name == "ck_orders_amount"
        assert op.removed_values == ()

    def test_missing_add_of_an_uncompilable_constraint_is_skipped(self, catalog: Any):
        """A type-bound constraint whose expression has no literal renderer is treated as unchanged, as elsewhere."""
        catalog({}, {})
        constraint = CheckConstraint(
            _jsonb_comparison(), name="ck_orders_payload", _type_bound=True, _create_rule=_always_created
        )
        table = _orders_table(constraint)
        # The reflected table holds the column, so the constraint reaches the compile step and fails there.
        reflected = Table("orders", MetaData(), Column("id", Integer, primary_key=True), Column("payload", JSONB()))

        assert self._run(table, conn_table=reflected).is_empty()

    def test_name_filter_vetoes_the_missing_add(self, catalog: Any):
        catalog({}, {})
        context = _stub_context(connection=object())
        context.name_filter_result = False

        assert self._run(_enum_orders_table(Status), context).is_empty()

    def test_object_filter_vetoes_the_missing_add(self, catalog: Any):
        catalog({}, {})
        context = _stub_context(connection=object())
        context.object_filter_result = False

        assert self._run(_enum_orders_table(Status), context).is_empty()


class TestUnnamedCheckConstraints:
    def test_table_and_column_constraints_collected_in_order(self):
        table = Table(
            "orders",
            MetaData(),
            Column("amount", Numeric(), CheckConstraint("amount > 0")),
            Column("qty", Integer),
            CheckConstraint("qty >= 0"),
        )

        assert [str(c.sqltext) for c in _unnamed_check_constraints(table, PG_DIALECT)] == ["amount > 0", "qty >= 0"]

    def test_order_is_creation_order_whatever_the_set_order(self):
        """``Table.constraints`` is a set; names chosen in that order would vary between processes."""
        expressions = [f"amount > {value}" for value in range(20)]
        table = _orders_table(*(CheckConstraint(expression) for expression in expressions))

        assert [str(c.sqltext) for c in _unnamed_check_constraints(table, PG_DIALECT)] == expressions

    def test_named_constraint_skipped(self):
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount"))

        assert _unnamed_check_constraints(table, PG_DIALECT) == []

    def test_constraint_a_naming_convention_names_skipped(self):
        metadata = MetaData(naming_convention={"ck": "ck_%(table_name)s_%(constraint_name)s"})
        table = _orders_table(CheckConstraint("amount > 0", name="amount"), metadata=metadata)

        assert _unnamed_check_constraints(table, PG_DIALECT) == []

    def test_unnamed_non_native_enum_constraint_collected(self):
        table = Table(
            "orders",
            MetaData(),
            Column("status", Enum("new", "done", native_enum=False, create_constraint=True, length=16)),
        )

        (constraint,) = _unnamed_check_constraints(table, PG_DIALECT)

        assert _compile_check_expression(constraint, PG_DIALECT) == "status IN ('new', 'done')"

    def test_boolean_constraint_skipped(self):
        table = Table("orders", MetaData(), Column("flag", Boolean(create_constraint=True)))

        assert _unnamed_check_constraints(table, PG_DIALECT) == []

    def test_enum_constraint_a_naming_convention_names_skipped(self):
        metadata = MetaData(naming_convention={"ck": "ck_%(table_name)s_%(column_0_name)s"})
        enum_type = Enum("new", "done", native_enum=False, create_constraint=True, length=16)
        table = Table("orders", metadata, Column("status", enum_type))

        assert _unnamed_check_constraints(table, PG_DIALECT) == []
        assert list(_metadata_check_constraints(table, PG_DIALECT)) == ["ck_orders_status"]


class TestUnnamedPairing(TestComparatorSkips):
    """Unnamed metadata constraints pair with reflected constraints by canonical expression."""

    @pytest.fixture
    def unnamed_catalog(self, monkeypatch: pytest.MonkeyPatch):
        """Stub the catalog, canonicalization by expression text, and the name probe."""

        def install(
            current: dict[str, str],
            canonical: dict[str, str] | None = None,
            *,
            names: Iterable[str | None] = (),
            not_valid: Iterable[str] = (),
        ) -> dict[str, list[Any]]:
            calls: dict[str, list[Any]] = {"canonicalized": [], "named": []}
            unvalidated = set(not_valid)
            chosen = list(names)

            def fake_inspect(_conn: object, **_kw: Any) -> list[CheckConstraintInfo]:
                return [
                    CheckConstraintInfo("public", "orders", name, expression, validated=name not in unvalidated)
                    for name, expression in sorted(current.items())
                ]

            def fake_canonicalize(_conn: object, **kwargs: Any) -> dict[str, str]:
                expressions: dict[str, str] = kwargs["expressions"]
                calls["canonicalized"].extend(expressions.values())
                lookup = canonical or {}
                return {label: lookup[text] for label, text in expressions.items() if text in lookup}

            def fake_choose(_conn: object, **kwargs: Any) -> list[str | None]:
                expressions: list[str] = kwargs["expressions"]
                calls["named"].append(list(expressions))
                return chosen[: len(expressions)]

            monkeypatch.setattr(module, "inspect_check_constraints", fake_inspect)
            monkeypatch.setattr(module, "canonicalize_check_constraints", fake_canonicalize)
            monkeypatch.setattr(module, "choose_check_constraint_names", fake_choose)
            return calls

        return install

    @staticmethod
    def _drop(name: str) -> DropConstraintOp:
        return DropConstraintOp(name, "orders", type_="check")

    def test_matching_text_pairs_without_a_round_trip(self, unnamed_catalog: Any):
        calls = unnamed_catalog({"orders_amount_check": "amount > 0"})
        spurious = self._drop("orders_amount_check")

        assert self._run(_orders_table(CheckConstraint("amount > 0")), existing=[spurious]).is_empty()
        assert calls == {"canonicalized": [], "named": []}

    def test_canonical_expression_pairs_and_discards_alembics_drop(self, unnamed_catalog: Any):
        calls = unnamed_catalog(
            {"orders_amount_check": "amount >= 0::numeric"}, {"amount >= 0": "amount >= 0::numeric"}
        )

        assert self._run(
            _orders_table(CheckConstraint("amount >= 0")), existing=[self._drop("orders_amount_check")]
        ).is_empty()
        assert calls["canonicalized"] == ["amount >= 0"]

    def test_unpaired_reflected_constraint_keeps_its_drop(self, unnamed_catalog: Any):
        unnamed_catalog({"orders_amount_check": "amount > 0", "orders_id_check": "id > 0"})
        drops = [self._drop("orders_amount_check"), self._drop("orders_id_check")]

        ops = self._run(_orders_table(CheckConstraint("amount > 0")), existing=drops).ops

        assert ops == [drops[1]]

    def test_unpaired_metadata_constraint_is_added_under_the_chosen_name(self, unnamed_catalog: Any):
        calls = unnamed_catalog({}, names=["orders_amount_check"])

        (op,) = self._run(_orders_table(CheckConstraint("amount > 0"))).ops

        assert type(op) is CreateCheckConstraintTextOp
        assert op.constraint_name == "orders_amount_check"
        assert str(op.condition) == "amount > 0"
        assert calls == {"canonicalized": [], "named": [["amount > 0"]]}

    def test_reverse_of_the_add_drops_the_chosen_name(self, unnamed_catalog: Any):
        unnamed_catalog({}, names=["orders_amount_check"])

        (op,) = self._run(_orders_table(CheckConstraint("amount > 0"))).ops
        reverse = op.reverse()

        assert isinstance(reverse, DropConstraintOp)
        assert reverse.constraint_name == "orders_amount_check"

    def test_unpaired_constraint_declaring_not_valid_is_added_not_valid(self, unnamed_catalog: Any):
        unnamed_catalog({}, names=["orders_amount_check"])

        (op,) = self._run(_orders_table(CheckConstraint("amount > 0", postgresql_not_valid=True))).ops

        assert type(op) is CreateCheckConstraintNotValidOp
        assert op.constraint_name == "orders_amount_check"

    def test_constraint_claimed_by_name_does_not_pair(self, unnamed_catalog: Any):
        """A named model constraint owns its catalog twin, so an unnamed copy of the same expression is new."""
        unnamed_catalog({"ck_orders_amount": "amount > 0"}, {"amount > 0": "amount > 0"}, names=["orders_amount_check"])
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount"), CheckConstraint("amount > 0"))

        (op,) = self._run(table).ops

        assert isinstance(op, CreateCheckConstraintOp)
        assert op.constraint_name == "orders_amount_check"

    def test_not_valid_pair_emits_validate_under_the_reflected_name(self, unnamed_catalog: Any):
        unnamed_catalog({"orders_amount_check": "amount > 0"}, not_valid=["orders_amount_check"])

        (op,) = self._run(
            _orders_table(CheckConstraint("amount > 0")), existing=[self._drop("orders_amount_check")]
        ).ops

        assert isinstance(op, ValidateConstraintOp)
        assert op.constraint_name == "orders_amount_check"

    def test_not_valid_pair_the_model_keeps_not_valid_emits_nothing(self, unnamed_catalog: Any):
        unnamed_catalog({"orders_amount_check": "amount > 0"}, not_valid=["orders_amount_check"])
        table = _orders_table(CheckConstraint("amount > 0", postgresql_not_valid=True))

        assert self._run(table).is_empty()

    def test_object_filter_can_veto_the_validation_of_a_pair(self, unnamed_catalog: Any):
        unnamed_catalog({"orders_amount_check": "amount > 0"}, not_valid=["orders_amount_check"])
        context = _stub_context(connection=object())
        context.object_filter_result = False

        assert self._run(_orders_table(CheckConstraint("amount > 0")), context).is_empty()

    def test_more_model_copies_than_catalog_copies_adds_the_rest(self, unnamed_catalog: Any, caplog: Any):
        unnamed_catalog({"orders_amount_check": "amount > 0"}, names=["orders_amount_check1"])
        table = _orders_table(CheckConstraint("amount > 0"), CheckConstraint("amount > 0"))

        with caplog.at_level(logging.WARNING, logger=module.__name__):
            (op,) = self._run(table, existing=[self._drop("orders_amount_check")]).ops

        assert isinstance(op, CreateCheckConstraintOp)
        assert op.constraint_name == "orders_amount_check1"
        assert "occurs 2 times in the model and 1 times" in caplog.text

    def test_more_catalog_copies_than_model_copies_keeps_a_drop(self, unnamed_catalog: Any, caplog: Any):
        unnamed_catalog({"orders_amount_check": "amount > 0", "orders_amount_check1": "amount > 0"})
        drops = [self._drop("orders_amount_check"), self._drop("orders_amount_check1")]

        with caplog.at_level(logging.WARNING, logger=module.__name__):
            ops = self._run(_orders_table(CheckConstraint("amount > 0")), existing=drops).ops

        assert ops == [drops[1]]
        assert "occurs 1 times in the model and 2 times" in caplog.text

    def test_failed_probe_keeps_the_leftover_constraints(self, unnamed_catalog: Any, caplog: Any):
        """Without a canonical form, the constraint may match a leftover catalog constraint, so nothing is dropped."""
        calls = unnamed_catalog({"orders_amount_check": "amount >= 0::numeric"}, {})
        drop = self._drop("orders_amount_check")

        with caplog.at_level(logging.WARNING, logger=module.__name__):
            assert self._run(_orders_table(CheckConstraint("amount >= 0")), existing=[drop]).is_empty()
        assert calls["named"] == []
        assert "Keeping check constraints ['orders_amount_check']" in caplog.text

    def test_failed_probe_of_one_constraint_still_pairs_the_others(self, unnamed_catalog: Any):
        """A rejected probe must not cost the other constraints their pairs."""
        unnamed_catalog({"orders_amount_check": "amount >= 0::numeric"}, {"amount >= 0": "amount >= 0::numeric"})
        table = _orders_table(CheckConstraint("amount >= 0"), CheckConstraint("qty > 0"))

        assert self._run(table, existing=[self._drop("orders_amount_check")]).is_empty()

    def test_failed_name_probe_adds_nothing(self, unnamed_catalog: Any):
        unnamed_catalog({}, names=[None])

        assert self._run(_orders_table(CheckConstraint("amount > 0"))).is_empty()

    def test_uncompilable_constraint_is_skipped(self, unnamed_catalog: Any):
        calls = unnamed_catalog({}, names=["orders_status_check"])
        table = _orders_table()
        # The expression references a column of this table, so only the compile step can reject it.
        table.append_constraint(CheckConstraint(table.c.status == bindparam("value", value={"a": 1}, type_=JSONB())))

        assert self._run(table).is_empty()
        assert calls["named"] == []

    def test_reflected_constraint_the_name_filter_excludes_still_pairs(self, unnamed_catalog: Any):
        """An excluded catalog constraint counts in the add decision, so no duplicate ``orders_amount_check1`` appears."""
        calls = unnamed_catalog({"orders_amount_check": "amount > 0"}, names=["orders_amount_check1"])
        context = _stub_context(connection=object())
        context.name_filter_result = _excludes_orders_amount_check

        assert self._run(_orders_table(CheckConstraint("amount > 0")), context).is_empty()
        assert calls["named"] == []

    def test_excluded_pair_emits_no_validation(self, unnamed_catalog: Any):
        unnamed_catalog({"orders_amount_check": "amount > 0"}, not_valid=["orders_amount_check"])
        context = _stub_context(connection=object())
        context.name_filter_result = _excludes_orders_amount_check

        assert self._run(_orders_table(CheckConstraint("amount > 0")), context).is_empty()

    def test_name_filter_can_veto_the_add(self, unnamed_catalog: Any):
        unnamed_catalog({}, names=["orders_amount_check"])
        context = _stub_context(connection=object())
        context.name_filter_result = False

        assert self._run(_orders_table(CheckConstraint("amount > 0")), context).is_empty()

    def test_object_filter_can_veto_the_add(self, unnamed_catalog: Any):
        unnamed_catalog({}, names=["orders_amount_check"])
        context = _stub_context(connection=object())
        context.object_filter_result = False

        assert self._run(_orders_table(CheckConstraint("amount > 0")), context).is_empty()

    def test_enum_constraint_of_a_column_alembic_is_adding_is_left_to_add_column(self, unnamed_catalog: Any):
        calls = unnamed_catalog({}, names=["orders_status_check"])
        table = Table(
            "orders",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("status", Enum("new", "done", native_enum=False, create_constraint=True, length=16)),
        )
        reflected = Table("orders", MetaData(), Column("id", Integer, primary_key=True))

        assert self._run(table, conn_table=reflected).is_empty()
        assert calls["named"] == []

    def test_object_filter_can_veto_a_value_set_change(self, unnamed_catalog: Any):
        unnamed_catalog(
            {"orders_status_check": WIDER_STATUS},
            {"status IN ('new', 'done')": CURRENT_STATUS},
            names=["orders_status_check1"],
        )
        context = _stub_context(connection=object())
        context.object_filter_result = False
        drop = self._drop("orders_status_check")
        table = Table(
            "orders",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("status", Enum("new", "done", native_enum=False, create_constraint=True, length=16)),
        )

        assert self._run(table, context, existing=[drop]).ops == [drop]

    def test_unnamed_enum_narrowing_drops_and_adds_not_valid_under_the_reflected_name(self, unnamed_catalog: Any):
        calls = unnamed_catalog({"orders_status_check": WIDER_STATUS}, {"status IN ('new', 'done')": CURRENT_STATUS})
        spurious = self._drop("orders_status_check")
        table = Table(
            "orders",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("status", Enum("new", "done", native_enum=False, create_constraint=True, length=16)),
        )

        ops = self._run(table, existing=[spurious]).ops

        assert [type(op) for op in ops] == [DropCheckConstraintOp, CreateCheckConstraintNotValidOp]
        assert ops[0] is not spurious
        add = ops[1]
        assert isinstance(add, CreateCheckConstraintNotValidOp)
        assert add.constraint_name == "orders_status_check"
        assert add.removed_values == ("shipped",)
        assert calls["named"] == []

    def test_validated_copy_pairs_before_a_not_valid_copy(self, unnamed_catalog: Any):
        """The duplicate that Alembic drops is the weaker ``NOT VALID`` copy, and the kept copy needs no validation."""
        unnamed_catalog(
            {"orders_amount_check": "amount > 0", "orders_amount_check1": "amount > 0"},
            not_valid=["orders_amount_check"],
        )
        drops = [self._drop("orders_amount_check"), self._drop("orders_amount_check1")]

        ops = self._run(_orders_table(CheckConstraint("amount > 0")), existing=drops).ops

        assert ops == [drops[0]]

    def test_column_expression_constraint_on_a_new_column_warns(self, unnamed_catalog: Any, caplog: Any):
        calls = unnamed_catalog({})
        table = _orders_table()
        qty = Column("qty", Integer)
        table.append_column(qty)
        table.append_constraint(CheckConstraint(qty > 0))
        reflected = _orders_table()

        with caplog.at_level(logging.WARNING, logger=module.__name__):
            assert self._run(table, conn_table=reflected).is_empty()

        assert calls["named"] == []
        assert "Unnamed check constraint 'qty > 0' on table 'orders' references a column" in caplog.text

    def test_unnamed_enum_constraint_pairs_with_the_catalog(self, unnamed_catalog: Any):
        unnamed_catalog({"orders_status_check": CURRENT_STATUS}, {"status IN ('new', 'done')": CURRENT_STATUS})
        table = Table(
            "orders",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("status", Enum("new", "done", native_enum=False, create_constraint=True, length=16)),
        )

        assert self._run(table, existing=[self._drop("orders_status_check")]).is_empty()


class TestValidationMode:
    def test_defaults_to_deferred(self):
        assert _validation_mode({}) == "deferred"

    @pytest.mark.parametrize("mode", ["deferred", "immediate"])
    def test_accepts_known_modes(self, mode: str):
        assert _validation_mode({VALIDATION_MODE_KEY: mode}) == mode

    def test_rejects_anything_else(self):
        with pytest.raises(ValueError, match="pg_check_constraint_validation"):
            _validation_mode({VALIDATION_MODE_KEY: "later"})


class TestValidationStateComparison(TestComparatorSkips):
    """A shared constraint whose expression is unchanged may still need ``VALIDATE CONSTRAINT``."""

    def test_not_valid_catalog_constraint_emits_validate(self, catalog: Any):
        probed = catalog({"ck_orders_amount": "amount > 0"}, {}, not_valid=["ck_orders_amount"])
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount"))

        (op,) = self._run(table).ops

        assert isinstance(op, ValidateConstraintOp)
        assert op.constraint_name == "ck_orders_amount"
        assert op.table_name == "orders"
        assert op.schema == "public"
        assert probed == []

    def test_canonicalized_match_still_emits_validate(self, catalog: Any):
        catalog(
            {"ck_orders_amount": "amount > 0::numeric"},
            {"ck_orders_amount": "amount > 0::numeric"},
            not_valid=["ck_orders_amount"],
        )
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount"))

        (op,) = self._run(table).ops

        assert isinstance(op, ValidateConstraintOp)

    def test_metadata_declaring_not_valid_emits_nothing(self, catalog: Any):
        catalog({"ck_orders_amount": "amount > 0"}, {}, not_valid=["ck_orders_amount"])
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount", postgresql_not_valid=True))

        assert self._run(table).is_empty()

    def test_validated_constraint_emits_nothing_whatever_the_metadata_says(self, catalog: Any):
        catalog({"ck_orders_amount": "amount > 0"}, {})
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount", postgresql_not_valid=True))

        assert self._run(table).is_empty()

    def test_immediate_mode_still_validates(self, catalog: Any):
        catalog({"ck_orders_amount": "amount > 0"}, {}, not_valid=["ck_orders_amount"])
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount"))

        (op,) = self._run(table, opts={VALIDATION_MODE_KEY: "immediate"}).ops

        assert isinstance(op, ValidateConstraintOp)

    def test_changed_expression_emits_no_validate(self, catalog: Any):
        catalog(
            {"ck_orders_amount": "amount > 0::numeric"},
            {"ck_orders_amount": "amount >= 0::numeric"},
            not_valid=["ck_orders_amount"],
        )
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount"))

        ops = self._run(table).ops

        assert [type(op) for op in ops] == [DropCheckConstraintOp, CreateCheckConstraintTextOp]
        drop = ops[0]
        assert isinstance(drop, DropConstraintOp)
        assert drop.to_constraint().dialect_options["postgresql"]["not_valid"] is True

    def test_object_filter_can_veto_validation(self, catalog: Any):
        catalog({"ck_orders_amount": "amount > 0"}, {}, not_valid=["ck_orders_amount"])
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount"))
        context = _stub_context(connection=object())
        context.object_filter_result = False

        assert self._run(table, context).is_empty()

    def test_invalid_mode_raises(self, catalog: Any):
        catalog({"ck_orders_amount": "amount > 0"}, {})
        table = _orders_table(CheckConstraint("amount > 0", name="ck_orders_amount"))

        with pytest.raises(ValueError, match="deferred"):
            self._run(table, opts={VALIDATION_MODE_KEY: "later"})


CURRENT_STATUS = "(status)::text = ANY ((ARRAY['new'::character varying, 'done'::character varying])::text[])"
WIDER_STATUS = (
    "(status)::text = ANY ((ARRAY['new'::character varying, 'done'::character varying, "
    "'shipped'::character varying])::text[])"
)
NARROWER_STATUS = "(status)::text = ANY ((ARRAY['new'::character varying])::text[])"
SHIFTED_STATUS = "(status)::text = ANY ((ARRAY['done'::character varying, 'shipped'::character varying])::text[])"


class TestValueSetChanges(TestComparatorSkips):
    """A value set that loses values is added ``NOT VALID`` in deferred mode; everything else keeps the validating add."""

    def test_widening_keeps_the_validating_add(self, catalog: Any):
        """No existing row can violate a wider set, so adding a value needs no second revision."""
        catalog({"ck_orders_status": CURRENT_STATUS}, {"ck_orders_status": WIDER_STATUS})

        ops = self._run(_enum_orders_table(WiderStatus)).ops

        assert [type(op) for op in ops] == [DropCheckConstraintOp, CreateCheckConstraintTextOp]

    def test_narrowing_records_removed_values(self, catalog: Any):
        catalog({"ck_orders_status": CURRENT_STATUS}, {"ck_orders_status": NARROWER_STATUS})
        table = _orders_table(CheckConstraint("status IN ('new')", name="ck_orders_status"))

        add = self._run(table).ops[1]

        assert isinstance(add, CreateCheckConstraintNotValidOp)
        assert add.column == "status"
        assert add.removed_values == ("done",)

    def test_disjoint_change_records_removed_values(self, catalog: Any):
        catalog({"ck_orders_status": CURRENT_STATUS}, {"ck_orders_status": SHIFTED_STATUS})
        table = _orders_table(CheckConstraint("status IN ('done', 'shipped')", name="ck_orders_status"))

        add = self._run(table).ops[1]

        assert isinstance(add, CreateCheckConstraintNotValidOp)
        assert add.removed_values == ("new",)

    def test_emitted_operations_never_attach_a_copy_to_the_metadata_table(self, catalog: Any):
        """Rendering and reversing the add must not leave a second ``ck_orders_status`` on the model's table.

        Regression test: an operation built from the bound column expression auto-attaches the constraint that
        ``to_constraint()`` creates to the metadata table, and the copy carries ``postgresql_not_valid=True``.  The
        next run in the same process then saw that copy, believed the model wanted ``NOT VALID``, and emitted no
        validation.
        """
        catalog({"ck_orders_status": WIDER_STATUS}, {"ck_orders_status": CURRENT_STATUS})
        table = _enum_orders_table(Status)

        # Alembic reflects the database into its own Table; the stub must not hand the comparator the model's.
        for op in self._run(table, conn_table=_orders_table()).ops:
            op.reverse()
            if isinstance(op, CreateCheckConstraintOp):
                op.to_constraint()

        checks = [c for c in table.constraints if isinstance(c, CheckConstraint)]
        assert len(checks) == 1
        assert checks[0].dialect_options["postgresql"]["not_valid"] is False

    def test_missing_add_never_attaches_a_copy_to_the_metadata_table(self, catalog: Any):
        catalog({}, {})
        table = _enum_orders_table(Status)

        (op,) = self._run(table).ops
        assert isinstance(op, CreateCheckConstraintOp)
        op.to_constraint()

        assert len([c for c in table.constraints if isinstance(c, CheckConstraint)]) == 1

    def test_non_value_set_change_keeps_the_validating_add(self, catalog: Any):
        catalog({"ck_orders_amount": "amount > 0::numeric"}, {"ck_orders_amount": "amount >= 0::numeric"})
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount"))

        ops = self._run(table).ops

        assert [type(op) for op in ops] == [DropCheckConstraintOp, CreateCheckConstraintTextOp]

    def test_immediate_mode_keeps_the_validating_add(self, catalog: Any):
        catalog({"ck_orders_status": WIDER_STATUS}, {"ck_orders_status": CURRENT_STATUS})

        ops = self._run(_enum_orders_table(Status), opts={VALIDATION_MODE_KEY: "immediate"}).ops

        assert [type(op) for op in ops] == [DropCheckConstraintOp, CreateCheckConstraintTextOp]

    def test_metadata_not_valid_uses_the_not_valid_add_for_any_expression(self, catalog: Any):
        catalog({"ck_orders_amount": "amount > 0::numeric"}, {"ck_orders_amount": "amount >= 0::numeric"})
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount", postgresql_not_valid=True))

        add = self._run(table, opts={VALIDATION_MODE_KEY: "immediate"}).ops[1]

        assert isinstance(add, CreateCheckConstraintNotValidOp)
        assert add.column is None
        assert add.removed_values == ()


class TestNewTableWarning:
    def test_unnamed_column_constraint_of_a_new_table_warns(self, caplog: pytest.LogCaptureFixture):
        table = Table(
            "orders",
            MetaData(),
            Column("amount", Numeric(), CheckConstraint("amount > 0"), CheckConstraint("amount < 9", name="ck_named")),
            Column("status", Enum("a", "b", native_enum=False, create_constraint=True)),
            CheckConstraint("amount <> 5"),
        )

        with caplog.at_level(logging.WARNING, logger=module.__name__):
            result = _compare_check_constraint_expressions(
                _stub_context(connection=object()), ModifyTableOps("orders", []), "public", "orders", None, table
            )

        assert result is PriorityDispatchResult.CONTINUE
        assert [record.getMessage() for record in caplog.records] == [
            "New table 'orders': Alembic's create_table does not create the unnamed check constraint on column "
            "'amount'. The next autogenerate run adds it."
        ]


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

    def test_registers_after_alembics_own_comparators(self):
        """The comparator must see the drop Alembic's check constraint plugin emits, so it runs last."""
        plugin = Plugin("test_alembic_pg_autogen_checkconstraints_priority")
        try:
            setup(plugin)
            registry = plugin.autogenerate_comparators._registry  # pyright: ignore[reportPrivateUsage]
            registered = [
                fn
                for (target, qualifier, priority), fns in registry.items()
                for fn, _ in fns
                if target == "table" and qualifier == "postgresql" and priority is DispatchPriority.LAST
            ]
        finally:
            plugin.remove()

        assert _compare_check_constraint_expressions in registered


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

    def test_non_native_enum_widening_renders_one_validating_statement(self, alembic_project: AlembicProject):
        _create_status_table(alembic_project, "'new', 'done'")

        content = _autogenerate_and_upgrade(alembic_project, _enum_orders_table(WiderStatus).metadata)

        assert "drop_constraint" in content
        assert "create_check_constraint" in content
        assert "'shipped'" in content
        assert "postgresql_not_valid" not in content
        assert "Backfill" not in content
        assert _status_constraint(alembic_project).validated is True

    def test_non_native_enum_narrowing_renders_backfill_comment(self, alembic_project: AlembicProject):
        _create_status_table(alembic_project, "'new', 'done', 'shipped'")

        content = _autogenerate(alembic_project, target_metadata=_enum_orders_table(Status).metadata)

        assert "postgresql_not_valid=True" in content
        assert "# ck_orders_status no longer allows status IN ('shipped')" in content
        assert "# Backfill those rows before the revision that validates ck_orders_status." in content

    def test_unchanged_enum_produces_nothing(self, alembic_project: AlembicProject):
        _create_status_table(alembic_project, "'new', 'done'")

        content = _autogenerate(alembic_project, target_metadata=_enum_orders_table(Status).metadata)

        assert "ck_orders_status" not in content

    def test_immediate_mode_keeps_one_validating_statement(self, alembic_project: AlembicProject):
        _create_status_table(alembic_project, "'new', 'done', 'shipped'")
        content = _autogenerate(
            alembic_project,
            target_metadata=_enum_orders_table(Status).metadata,
            pg_check_constraint_validation="immediate",
        )

        assert "drop_constraint" in content
        assert "create_check_constraint" in content
        assert "postgresql_not_valid" not in content

    def test_not_valid_catalog_constraint_is_validated(self, alembic_project: AlembicProject):
        _create_status_table(alembic_project, "'new', 'done'", not_valid=True)

        content = _autogenerate(alembic_project, target_metadata=_enum_orders_table(Status).metadata)

        assert "VALIDATE CONSTRAINT ck_orders_status" in content
        assert "drop_constraint" not in content
        assert "pass  # Constraint ck_orders_status on orders stays validated." in content

    def test_metadata_not_valid_keeps_the_catalog_not_valid(self, alembic_project: AlembicProject):
        _create_status_table(alembic_project, "'new', 'done'", not_valid=True)
        metadata = MetaData()
        Table(
            "orders",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("status", String(16)),
            CheckConstraint("status IN ('new', 'done')", name="ck_orders_status", postgresql_not_valid=True),
        )

        content = _autogenerate(alembic_project, target_metadata=metadata)

        assert "ck_orders_status" not in content

    def test_three_runs_converge(self, alembic_project: AlembicProject):
        """Run one adds NOT VALID, run two validates, run three has nothing left to do."""
        _create_status_table(alembic_project, "'new', 'done', 'shipped'")
        metadata = _enum_orders_table(Status).metadata

        first = _autogenerate_and_upgrade(alembic_project, metadata)
        assert "postgresql_not_valid=True" in first
        assert _status_constraint(alembic_project).validated is False

        second = _autogenerate_and_upgrade(alembic_project, metadata)
        assert "VALIDATE CONSTRAINT ck_orders_status" in second
        assert "drop_constraint" not in second
        assert _status_constraint(alembic_project).validated is True

        third = _autogenerate_and_upgrade(alembic_project, metadata)
        assert "ck_orders_status" not in third

    def test_narrowing_validation_fails_until_the_backfill_runs(self, alembic_project: AlembicProject):
        _create_status_table(alembic_project, "'new', 'done', 'shipped'")
        alembic_project.execute("INSERT INTO orders (status) VALUES ('shipped')")
        metadata = _enum_orders_table(Status).metadata

        first = _autogenerate_and_upgrade(alembic_project, metadata)
        assert "Backfill" in first

        # The NOT VALID constraint already rejects new rows that hold a removed value.
        with alembic_project.connect() as conn, pytest.raises(exc.IntegrityError), conn.begin_nested():
            conn.execute(text("INSERT INTO orders (status) VALUES ('shipped')"))

        with pytest.raises(exc.IntegrityError):
            _autogenerate_and_upgrade(alembic_project, metadata)
        assert _status_constraint(alembic_project).validated is False

        alembic_project.execute("UPDATE orders SET status = 'done' WHERE status = 'shipped'")
        _upgrade(alembic_project, metadata)
        assert _status_constraint(alembic_project).validated is True

    def test_alembics_byname_plugin_does_not_drop_the_enum_constraint(self, alembic_project: AlembicProject):
        """Alembic's plugin reports the catalog's copy of a type-bound constraint as removed; ours discards that."""
        _create_status_table(alembic_project, "'new', 'done'")

        content = _autogenerate(
            alembic_project,
            target_metadata=_enum_orders_table(Status).metadata,
            autogenerate_plugins=[
                "alembic.autogenerate.*",
                "alembic.autogenerate.checkconstraint_byname",
                "alembic_pg_autogen.*",
            ],
        )

        assert "ck_orders_status" not in content

    def test_missing_enum_constraint_is_added(self, alembic_project: AlembicProject):
        """``create_constraint`` defaults to False; a model that turns it on gets the constraint created."""
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY, status varchar(16))")

        content = _autogenerate(alembic_project, target_metadata=_enum_orders_table(Status).metadata)

        upgrade_body = _upgrade_body(content)
        assert "create_check_constraint" in upgrade_body
        assert "ck_orders_status" in upgrade_body
        assert "drop_constraint" not in upgrade_body

    def test_missing_enum_constraint_is_added_once_with_alembics_plugin(self, alembic_project: AlembicProject):
        """Alembic 1.20 also adds a missing type-bound constraint; the migration must hold one add."""
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY, status varchar(16))")
        metadata = _enum_orders_table(Status).metadata

        content = _upgrade_body(
            _autogenerate_and_upgrade(alembic_project, metadata, autogenerate_plugins=_BYNAME_PLUGINS)
        )

        assert content.count("create_check_constraint('ck_orders_status'") == 1
        assert _check_constraint_names(alembic_project) == ["ck_orders_status"]
        assert "ck_orders_status" not in _upgrade_body(
            _autogenerate_and_upgrade(alembic_project, metadata, autogenerate_plugins=_BYNAME_PLUGINS)
        )

    def test_new_enum_column_upgrades_with_alembics_plugin(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY)")
        metadata = _enum_orders_table(Status).metadata

        content = _upgrade_body(
            _autogenerate_and_upgrade(alembic_project, metadata, autogenerate_plugins=_BYNAME_PLUGINS)
        )

        assert "add_column" in content
        assert "create_check_constraint" not in content
        assert _check_constraint_names(alembic_project) == ["ck_orders_status"]

    def test_new_enum_column_upgrades_with_one_constraint(self, alembic_project: AlembicProject):
        """Regression test: the comparator once added the constraint that ``add_column`` already creates."""
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY)")
        metadata = _enum_orders_table(Status).metadata

        content = _autogenerate_and_upgrade(alembic_project, metadata)

        assert "add_column" in _upgrade_body(content)
        assert "create_check_constraint" not in _upgrade_body(content)
        assert _status_constraint(alembic_project).validated is True
        assert "ck_orders_status" not in _autogenerate_and_upgrade(alembic_project, metadata)

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

    def test_unnamed_constraints_get_no_spurious_drops(self, alembic_project: AlembicProject):
        """Alembic's plugin drops every unnamed model constraint; only the constraint from raw SQL should go."""
        alembic_project.execute(
            "CREATE TABLE orders (id serial PRIMARY KEY, amount numeric CHECK (amount > 0), qty int,"
            " status varchar(16) CHECK (status IN ('new', 'done')), CHECK (qty >= 0), CHECK (qty <= amount))"
        )
        alembic_project.execute("ALTER TABLE orders ADD CHECK (id > 0)")

        content = _autogenerate(
            alembic_project,
            target_metadata=_unnamed_orders_table().metadata,
            autogenerate_plugins=[
                "alembic.autogenerate.*",
                "alembic.autogenerate.checkconstraint_byname",
                "alembic_pg_autogen.*",
            ],
        )

        upgrade_body = _upgrade_body(content)
        assert _dropped_names(upgrade_body) == ["orders_id_check"]
        assert "create_check_constraint" not in upgrade_body

    def test_missing_unnamed_constraint_is_added_under_the_postgresql_name(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY, amount numeric CHECK (amount > 0))")
        metadata = MetaData()
        Table(
            "orders",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric(), CheckConstraint("amount > 0"), CheckConstraint("amount < 100")),
        )

        content = _autogenerate_and_upgrade(alembic_project, metadata)

        assert "op.create_check_constraint('orders_amount_check1', 'orders', 'amount < 100')" in content
        assert "op.drop_constraint('orders_amount_check1', 'orders', type_='check')" in content
        assert _check_constraint_names(alembic_project) == ["orders_amount_check", "orders_amount_check1"]
        assert "check" not in _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))

        downgrade(alembic_project.config, "-2")
        assert _check_constraint_names(alembic_project) == ["orders_amount_check"]

    def test_not_valid_unnamed_catalog_constraint_is_validated(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY, amount numeric)")
        alembic_project.execute("ALTER TABLE orders ADD CHECK (amount >= 0) NOT VALID")
        metadata = MetaData()
        Table("orders", metadata, Column("id", Integer, primary_key=True), Column("amount", Numeric()))
        metadata.tables["orders"].append_constraint(CheckConstraint("amount >= 0"))

        content = _autogenerate_and_upgrade(alembic_project, metadata)

        assert "VALIDATE CONSTRAINT orders_amount_check" in content
        assert "drop_constraint" not in _upgrade_body(content)
        with alembic_project.connect() as conn:
            (info,) = inspect_check_constraints(conn, schemas=[alembic_project.schema], table_names=["orders"])
        assert info.validated is True

    def test_extra_catalog_copy_of_an_unnamed_constraint_is_dropped(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY, amount numeric)")
        alembic_project.execute("ALTER TABLE orders ADD CHECK (amount > 0)")
        alembic_project.execute("ALTER TABLE orders ADD CHECK (amount > 0)")
        metadata = MetaData()
        Table(
            "orders",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric(), CheckConstraint("amount > 0")),
        )

        content = _autogenerate(
            alembic_project,
            target_metadata=metadata,
            autogenerate_plugins=[
                "alembic.autogenerate.*",
                "alembic.autogenerate.checkconstraint_byname",
                "alembic_pg_autogen.*",
            ],
        )

        assert _dropped_names(_upgrade_body(content)) == ["orders_amount_check1"]

    def test_unnamed_constraint_on_a_new_column_is_added_on_the_next_run(
        self, alembic_project: AlembicProject, caplog: pytest.LogCaptureFixture
    ):
        """PostgreSQL cannot test ``qty >= 0`` before ``qty`` exists, and ``add_column`` does not create it."""
        alembic_project.execute("CREATE TABLE orders (id serial PRIMARY KEY, amount numeric)")
        metadata = MetaData()
        Table(
            "orders",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric(), CheckConstraint("amount > 0")),
            Column("qty", Integer, CheckConstraint("qty >= 0")),
        )

        with caplog.at_level(logging.WARNING, logger=module.__name__):
            first = _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))
        assert "add_column" in first
        assert "create_check_constraint('orders_amount_check'" in first
        assert "qty >= 0" not in first
        assert "Could not add unnamed check constraint 'qty >= 0' on table 'orders'" in caplog.text

        second = _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))
        assert "op.create_check_constraint('orders_qty_check', 'orders', 'qty >= 0')" in second
        assert _check_constraint_names(alembic_project) == ["orders_amount_check", "orders_qty_check"]

    def test_unnamed_column_constraint_of_a_new_table_is_added_on_the_next_run(
        self, alembic_project: AlembicProject, caplog: pytest.LogCaptureFixture
    ):
        """Alembic's ``create_table`` leaves column-level constraints out; table-level ones and types keep theirs."""
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric(), CheckConstraint("amount > 0")),
            Column("status", Enum("a", "b", native_enum=False, create_constraint=True)),
            CheckConstraint("amount < 100"),
        )

        with caplog.at_level(logging.WARNING, logger=module.__name__):
            first = _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))
        assert "create_table" in first
        assert (
            "New table 'o': Alembic's create_table does not create the unnamed check constraint on column 'amount'"
            in (caplog.text)
        )
        assert "column 'status'" not in caplog.text
        assert _check_constraint_names(alembic_project, "o") == ["o_amount_check", "o_status_check"]

        second = _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))
        assert "op.create_check_constraint('o_amount_check1', 'o', 'amount > 0')" in second
        assert "check" not in _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))

    def test_posix_character_class_round_trips(self, alembic_project: AlembicProject):
        """``[[:alpha:]]`` holds a colon that the bind pattern of ``text()`` must not take."""
        alembic_project.execute("CREATE TABLE o (id serial PRIMARY KEY, a text CHECK (a ~ '^[[:alpha:]]+$'))")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("a", Text(), CheckConstraint("a ~ '^[[:alpha:]]+$'")),
        )

        assert "check" not in _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))

    def test_missing_posix_character_class_constraint_is_added_exactly(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE o (id serial PRIMARY KEY, a text)")
        alembic_project.execute("INSERT INTO o (a) VALUES ('abc')")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("a", Text(), CheckConstraint("a ~ '^[[:alpha:]]+$'")),
        )

        content = _autogenerate_and_upgrade(alembic_project, metadata)

        assert """op.create_check_constraint('o_a_check', 'o', "a ~ '^[[:alpha:]]+$'")""" in content
        assert _check_constraint_expressions(alembic_project, "o") == ["a ~ '^[[:alpha:]]+$'::text"]
        assert "check" not in _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))

    def test_changed_named_posix_character_class_constraint_upgrades(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE o (id serial PRIMARY KEY, a text, CONSTRAINT ck_o_a CHECK (a ~ '^x'))")
        alembic_project.execute("INSERT INTO o (a) VALUES ('xyz')")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("a", Text()),
            CheckConstraint("a ~ '^[[:alpha:]]+$'", name="ck_o_a"),
        )

        content = _autogenerate_and_upgrade(alembic_project, metadata)

        assert """op.create_check_constraint('ck_o_a', 'o', "a ~ '^[[:alpha:]]+$'")""" in content
        assert _check_constraint_expressions(alembic_project, "o") == ["a ~ '^[[:alpha:]]+$'::text"]

    def test_function_of_the_row_type_probes_on_the_real_table(self, alembic_project: AlembicProject):
        """``ok(o)`` takes the row type of ``o``; the ``TEMP`` copy has its own row type, so the probe retries."""
        alembic_project.execute("CREATE TABLE o (id serial PRIMARY KEY, a int)")
        alembic_project.execute("CREATE FUNCTION ok(o) RETURNS boolean LANGUAGE sql IMMUTABLE AS 'SELECT true'")
        alembic_project.execute("ALTER TABLE o ADD CONSTRAINT nm CHECK (ok(o) AND a > 0)")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("a", Integer),
            CheckConstraint("ok(o) AND a > 1", name="nm"),
            CheckConstraint("ok(o)"),
        )

        content = _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))

        assert _dropped_names(content) == ["nm"]
        assert "op.create_check_constraint('nm', 'o', 'ok(o) AND a > 1')" in content
        assert "op.create_check_constraint('o_check', 'o', 'ok(o)')" in content
        assert "check" not in _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))

    def test_changed_constraint_under_a_naming_convention_upgrades_and_downgrades(
        self, alembic_project: AlembicProject
    ):
        """Without ``op.f()``, the convention applies again at upgrade time and names ``ck_o_ck_o_foo``."""
        alembic_project.execute("CREATE TABLE o (id serial PRIMARY KEY, a int, CONSTRAINT ck_o_foo CHECK (a > 0))")
        metadata = MetaData(naming_convention={"ck": "ck_%(table_name)s_%(constraint_name)s"})
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("a", Integer),
            CheckConstraint("a > 1", name="foo"),
        )

        content = _autogenerate_and_upgrade(alembic_project, metadata, autogenerate_plugins=_BYNAME_PLUGINS)

        assert "op.drop_constraint(op.f('ck_o_foo'), 'o', type_='check')" in content
        assert "op.create_check_constraint(op.f('ck_o_foo'), 'o', 'a > 1')" in content
        assert _check_constraint_expressions(alembic_project, "o") == ["a > 1"]
        assert "check" not in _upgrade_body(
            _autogenerate_and_upgrade(alembic_project, metadata, autogenerate_plugins=_BYNAME_PLUGINS)
        )

        downgrade(alembic_project.config, "-2")
        assert _check_constraint_names(alembic_project, "o") == ["ck_o_foo"]
        assert _check_constraint_expressions(alembic_project, "o") == ["a > 0"]

    def test_unnamed_value_set_change_under_a_naming_convention_upgrades(self, alembic_project: AlembicProject):
        """A convention without ``%(constraint_name)s`` leaves an unnamed ``Enum`` unnamed; the reflected name is final."""
        alembic_project.execute(
            "CREATE TABLE o (id serial PRIMARY KEY, status varchar(16) CHECK (status IN ('a', 'b', 'c')))"
        )
        metadata = MetaData(naming_convention={"ix": "ix_%(column_0_label)s"})
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("status", Enum("a", "b", native_enum=False, create_constraint=True, length=16)),
        )

        _autogenerate_and_upgrade(alembic_project, metadata, autogenerate_plugins=_BYNAME_PLUGINS)

        assert _check_constraint_names(alembic_project, "o") == ["o_status_check"]

    def test_percent_in_an_unnamed_constraint_round_trips(self, alembic_project: AlembicProject):
        """Compiled SQL doubles ``%``; the comparator must compare and render the single form."""
        alembic_project.execute(
            "CREATE TABLE o (id serial PRIMARY KEY, code varchar(9) CHECK (code LIKE 'A%'), q int CHECK (q % 2 = 0))"
        )
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("code", String(9), CheckConstraint("code LIKE 'A%'")),
            Column("q", Integer, CheckConstraint("q % 2 = 0")),
        )

        assert "check" not in _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))

    def test_percent_in_a_new_enum_value_set_is_added_once(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE o (id serial PRIMARY KEY, code varchar(9))")
        alembic_project.execute("INSERT INTO o (code) VALUES ('10%')")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("code", Enum("10%", "20%", native_enum=False, create_constraint=True, length=9)),
        )

        content = _autogenerate_and_upgrade(alembic_project, metadata)

        assert "op.create_check_constraint('o_code_check', 'o', \"code IN ('10%', '20%')\")" in content
        assert "check" not in _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))

    def test_colon_in_an_enum_value_is_not_a_bind_parameter(self, alembic_project: AlembicProject):
        # ``execute()`` runs the SQL through ``text()``, so the test escapes its own colon.
        alembic_project.execute(
            "CREATE TABLE o (id serial PRIMARY KEY, code varchar(9) CHECK (code IN ('a \\:b', 'c')))"
        )
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("code", Enum("a :b", "c", native_enum=False, create_constraint=True, length=9)),
        )

        assert "check" not in _upgrade_body(_autogenerate_and_upgrade(alembic_project, metadata))

    def test_rejected_probe_keeps_the_other_pairs(self, alembic_project: AlembicProject):
        """A new column's constraint fails its probe; the existing constraint must keep its pair and its catalog row."""
        alembic_project.execute("CREATE TABLE o (id serial PRIMARY KEY, amount numeric CHECK (amount >= 0))")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric(), CheckConstraint("amount >= 0")),
            Column("qty", Integer, CheckConstraint("qty > 0")),
        )

        first = _upgrade_body(
            _autogenerate_and_upgrade(alembic_project, metadata, autogenerate_plugins=_BYNAME_PLUGINS)
        )

        assert "drop_constraint" not in first
        assert "add_column" in first
        assert _check_constraint_names(alembic_project, "o") == ["o_amount_check"]

    def test_unnamed_add_avoids_a_named_add_of_the_same_migration(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE o (id serial PRIMARY KEY, amount numeric)")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric()),
            CheckConstraint("amount > 0", name="o_amount_check"),
            CheckConstraint("amount < 100"),
        )

        content = _autogenerate_and_upgrade(alembic_project, metadata, autogenerate_plugins=_BYNAME_PLUGINS)

        assert "op.create_check_constraint('o_amount_check1', 'o', 'amount < 100')" in content
        assert _check_constraint_names(alembic_project, "o") == ["o_amount_check", "o_amount_check1"]

    def test_unnamed_add_avoids_a_constraint_name_of_another_table(self, alembic_project: AlembicProject):
        """PostgreSQL avoids constraint names across the whole schema, and the TEMP copy cannot see them."""
        alembic_project.execute("CREATE TABLE other (a int CONSTRAINT o_amount_check CHECK (a > 0))")
        alembic_project.execute("CREATE TABLE o (id serial PRIMARY KEY, amount numeric)")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric(), CheckConstraint("amount > 0")),
        )

        content = _autogenerate_and_upgrade(alembic_project, metadata)

        assert "op.create_check_constraint('o_amount_check1', 'o', 'amount > 0')" in content

    def test_unnamed_enum_narrowing_is_added_not_valid(self, alembic_project: AlembicProject):
        alembic_project.execute(
            "CREATE TABLE o (id serial PRIMARY KEY, status varchar(16) CHECK (status IN ('a', 'b', 'c')))"
        )
        alembic_project.execute("INSERT INTO o (status) VALUES ('c')")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("status", Enum("a", "b", native_enum=False, create_constraint=True, length=16)),
        )

        content = _autogenerate_and_upgrade(alembic_project, metadata, autogenerate_plugins=_BYNAME_PLUGINS)

        upgrade_body = _upgrade_body(content)
        assert _dropped_names(upgrade_body) == ["o_status_check"]
        assert (
            "create_check_constraint(op.f('o_status_check'), 'o', \"status IN ('a', 'b')\", postgresql_not_valid=True)"
            in (upgrade_body)
        )
        assert "# Backfill those rows before the revision that validates o_status_check." in upgrade_body
        with alembic_project.connect() as conn:
            (info,) = inspect_check_constraints(conn, schemas=[alembic_project.schema], table_names=["o"])
        assert info.validated is False

    def test_excluded_catalog_constraint_is_not_added_again(self, alembic_project: AlembicProject):
        alembic_project.execute("CREATE TABLE o (id serial PRIMARY KEY, amount numeric CHECK (amount > 0))")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("amount", Numeric(), CheckConstraint("amount > 0")),
        )

        content = _autogenerate(
            alembic_project,
            target_metadata=metadata,
            autogenerate_plugins=_BYNAME_PLUGINS,
            include_name=_excludes_o_amount_check,
        )

        assert "check" not in _upgrade_body(content)

    def test_downgrade_restores_percent_and_colon_exactly(self, alembic_project: AlembicProject):
        # ``execute()`` runs the SQL through ``text()``, so the test escapes its own colon.
        alembic_project.execute(
            "CREATE TABLE o (id serial PRIMARY KEY, code varchar(9),"
            " CONSTRAINT ck_o_code CHECK (code LIKE 'A%' OR code = 'a \\:b'))"
        )
        before = _check_constraint_expressions(alembic_project, "o")
        metadata = MetaData()
        Table(
            "o",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("code", String(9)),
            CheckConstraint("code LIKE 'B%'", name="ck_o_code"),
        )

        content = _autogenerate_and_upgrade(alembic_project, metadata)
        assert "op.create_check_constraint('ck_o_code', 'o', \"code LIKE 'B%'\")" in content
        assert _check_constraint_expressions(alembic_project, "o") == ["code::text ~~ 'B%'::text"]

        downgrade(alembic_project.config, "-1")
        assert _check_constraint_expressions(alembic_project, "o") == before


_BYNAME_PLUGINS: Final = [
    "alembic.autogenerate.*",
    "alembic.autogenerate.checkconstraint_byname",
    "alembic_pg_autogen.*",
]


def _excludes_orders_amount_check(name: str) -> bool:
    return name != "orders_amount_check"


def _excludes_o_amount_check(name: str | None, type_: str, _parent_names: object) -> bool:
    return not (type_ == "check_constraint" and name == "o_amount_check")


def _unnamed_orders_table() -> Table:
    """The ``orders`` model of the proposal: four unnamed constraints, one of them from a non-native ``Enum``."""
    return Table(
        "orders",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("amount", Numeric(), CheckConstraint("amount > 0")),
        Column("qty", Integer),
        Column("status", Enum("new", "done", native_enum=False, create_constraint=True, length=16)),
        CheckConstraint("qty >= 0"),
        CheckConstraint("qty <= amount"),
    )


def _dropped_names(body: str) -> list[str]:
    """Return the constraint names of the ``drop_constraint`` calls, with or without Alembic's ``op.f()`` marker."""
    return re.findall(r"drop_constraint\((?:op\.f\()?'(\w+)'", body)


def _check_constraint_expressions(project: AlembicProject, table_name: str) -> list[str]:
    with project.connect() as conn:
        infos = inspect_check_constraints(conn, schemas=[project.schema], table_names=[table_name])
    return [info.expression for info in infos]


def _check_constraint_names(project: AlembicProject, table_name: str = "orders") -> list[str]:
    with project.connect() as conn:
        infos = inspect_check_constraints(conn, schemas=[project.schema], table_names=[table_name])
    return [info.name for info in infos]


def _upgrade_body(content: str) -> str:
    match = re.search(r"def upgrade\(\).*?(?=def downgrade\(\))", content, re.DOTALL)
    assert match is not None
    return match.group(0)


def _create_status_table(project: AlembicProject, values: str, *, not_valid: bool = False) -> None:
    project.execute("CREATE TABLE orders (id serial PRIMARY KEY, status varchar(16))")
    suffix = " NOT VALID" if not_valid else ""
    project.execute(f"ALTER TABLE orders ADD CONSTRAINT ck_orders_status CHECK (status IN ({values})){suffix}")


def _status_constraint(project: AlembicProject) -> CheckConstraintInfo:
    with project.connect() as conn:
        (info,) = inspect_check_constraints(conn, schemas=[project.schema], table_names=["orders"])
    return info


def _autogenerate_and_upgrade(project: AlembicProject, metadata: MetaData, **attrs: object) -> str:
    """Autogenerate the next revision, apply it, and return its source."""
    cfg = project.config
    cfg.attributes["target_metadata"] = metadata
    cfg.attributes.update(attrs)
    versions = Path(cfg.get_main_option("script_location")) / "versions"  # pyright: ignore[reportArgumentType]
    before = set(versions.glob("*.py"))
    script = revision(cfg, message="test", autogenerate=True)
    assert script is not None
    (created,) = set(versions.glob("*.py")) - before
    _upgrade(project, metadata)
    return created.read_text()


def _upgrade(project: AlembicProject, metadata: MetaData) -> None:
    cfg = project.config
    cfg.attributes["target_metadata"] = metadata
    upgrade(cfg, "head")
