# These tests exercise the comparator's internals directly.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import enum
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic.command import revision, upgrade
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
    bindparam,
    exc,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from alembic_pg_autogen import CheckConstraintInfo, CreateCheckConstraintNotValidOp, ValidateConstraintOp
from alembic_pg_autogen import compare_check_constraints as module
from alembic_pg_autogen.compare_check_constraints import (
    VALIDATION_MODE_KEY,
    _compare_check_constraint_expressions,
    _compile_check_expression,
    _metadata_check_constraints,
    _same_sql,
    _validation_mode,
    setup,
)
from alembic_pg_autogen.inspect import inspect_check_constraints

from .test_autogenerate import _autogenerate

if TYPE_CHECKING:
    from collections.abc import Iterable

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
    name_filter_result: bool
    object_filter_result: bool

    def __init__(self, connection: object | None = None, opts: dict[str, Any] | None = None) -> None:
        self.connection = connection
        self.dialect = PG_DIALECT
        self.opts = opts if opts is not None else {}
        self.name_filter_result = True
        self.object_filter_result = True

    def run_name_filters(self, *_args: Any, **_kw: Any) -> bool:
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
        ],
    )
    def test_whitespace_insensitive_comparison(self, left: str, right: str, expected: bool):
        assert _same_sql(left, right) is expected


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

        assert [type(op).__name__ for op in ops] == ["DropConstraintOp", "CreateCheckConstraintOp"]


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
        catalog({"ck_orders_status": CURRENT_STATUS}, {"ck_orders_status": WIDER_STATUS})
        spurious = DropConstraintOp("ck_orders_status", "orders", type_="check")

        ops = self._run(_enum_orders_table(WiderStatus), existing=[spurious]).ops

        assert [type(op) for op in ops] == [DropConstraintOp, CreateCheckConstraintNotValidOp]
        assert ops[0] is not spurious

    def test_missing_enum_constraint_is_added(self, catalog: Any):
        probed = catalog({}, {})

        (op,) = self._run(_enum_orders_table(Status)).ops

        assert type(op) is CreateCheckConstraintOp
        assert op.constraint_name == "ck_orders_status"
        assert probed == []

    def test_missing_add_of_an_uncompilable_constraint_is_skipped(self, catalog: Any):
        """A type-bound constraint whose expression has no literal renderer is treated as unchanged, as elsewhere."""
        catalog({}, {})
        constraint = CheckConstraint(
            _jsonb_comparison(), name="ck_orders_payload", _type_bound=True, _create_rule=_always_created
        )
        table = _orders_table(constraint)

        assert self._run(table).is_empty()

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

        assert [type(op) for op in ops] == [DropConstraintOp, CreateCheckConstraintOp]
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
    """A changed value set is added ``NOT VALID`` in deferred mode; everything else keeps the validating add."""

    def test_widening_emits_not_valid_add(self, catalog: Any):
        catalog({"ck_orders_status": CURRENT_STATUS}, {"ck_orders_status": WIDER_STATUS})

        ops = self._run(_enum_orders_table(WiderStatus)).ops

        assert [type(op) for op in ops] == [DropConstraintOp, CreateCheckConstraintNotValidOp]
        add = ops[1]
        assert isinstance(add, CreateCheckConstraintNotValidOp)
        assert add.column == "status"
        assert add.removed_values == ()
        assert add.kw["postgresql_not_valid"] is True

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
        catalog({"ck_orders_status": CURRENT_STATUS}, {"ck_orders_status": WIDER_STATUS})
        table = _enum_orders_table(WiderStatus)

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

        assert [type(op) for op in ops] == [DropConstraintOp, CreateCheckConstraintOp]

    def test_immediate_mode_keeps_the_validating_add(self, catalog: Any):
        catalog({"ck_orders_status": CURRENT_STATUS}, {"ck_orders_status": WIDER_STATUS})

        ops = self._run(_enum_orders_table(WiderStatus), opts={VALIDATION_MODE_KEY: "immediate"}).ops

        assert [type(op) for op in ops] == [DropConstraintOp, CreateCheckConstraintOp]

    def test_metadata_not_valid_uses_the_not_valid_add_for_any_expression(self, catalog: Any):
        catalog({"ck_orders_amount": "amount > 0::numeric"}, {"ck_orders_amount": "amount >= 0::numeric"})
        table = _orders_table(CheckConstraint("amount >= 0", name="ck_orders_amount", postgresql_not_valid=True))

        add = self._run(table, opts={VALIDATION_MODE_KEY: "immediate"}).ops[1]

        assert isinstance(add, CreateCheckConstraintNotValidOp)
        assert add.column is None
        assert add.removed_values == ()


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

    def test_non_native_enum_widening_renders_not_valid(self, alembic_project: AlembicProject):
        _create_status_table(alembic_project, "'new', 'done'")

        content = _autogenerate(alembic_project, target_metadata=_enum_orders_table(WiderStatus).metadata)

        assert "drop_constraint" in content
        assert "postgresql_not_valid=True" in content
        assert "'shipped'" in content
        assert "Backfill" not in content
        assert "VALIDATE CONSTRAINT" not in content

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
        _create_status_table(alembic_project, "'new', 'done'")

        content = _autogenerate(
            alembic_project,
            target_metadata=_enum_orders_table(WiderStatus).metadata,
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
        _create_status_table(alembic_project, "'new', 'done'")
        metadata = _enum_orders_table(WiderStatus).metadata

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


def _autogenerate_and_upgrade(project: AlembicProject, metadata: MetaData) -> str:
    """Autogenerate the next revision, apply it, and return its source."""
    cfg = project.config
    cfg.attributes["target_metadata"] = metadata
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
