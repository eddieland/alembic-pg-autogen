"""Comparator that verifies ``CHECK`` constraint expressions during autogenerate.

Alembic detects when a named ``CHECK`` constraint is added to or removed from your models, but two constraints that
share a name are always presumed equivalent: normalizing an arbitrary SQL expression is not something Alembic can do
in a backend-agnostic way.  This module closes that gap for PostgreSQL by asking PostgreSQL itself.  Each metadata
expression is round-tripped through the server and compared against the catalog's own deparsed form, so a changed
``CHECK`` expression produces a ``DROP CONSTRAINT`` / ``ADD CONSTRAINT`` pair instead of silently drifting.

A set of allowed values that loses a value, such as the constraint a non-native ``Enum`` produces when a member goes
away, gets a different pair.  The new constraint is added ``NOT VALID``, which PostgreSQL enforces for new rows at once
but does not scan the table for, so rows that hold a removed value can be backfilled first.  The next autogenerate run
reads ``convalidated`` from the catalog and emits the ``VALIDATE CONSTRAINT``.  Adding a value keeps one validating
statement, because no existing row can violate a wider set.  Set ``pg_check_constraint_validation="immediate"`` to keep
one validating statement for every change.

This comparator complements Alembic's ``alembic.autogenerate.checkconstraint_byname`` rather than replacing it, and
both are needed: that plugin owns names present on only one side (added and removed constraints), while this one owns
names present on both, which is the case it cannot decide.  The two sets are disjoint, so no operation is emitted
twice.

The one exception is a constraint that a type generates, such as the one a non-native ``Enum`` derives from its
members.  Alembic's plugin leaves those out of its metadata side entirely, so it reports the catalog's copy as removed
and never adds a missing one.  This comparator therefore owns those constraints end to end: it runs after Alembic's
plugin, discards that spurious drop, and adds the constraint when the catalog lacks it.

A constraint without a name is the second exception.  PostgreSQL names every constraint, so Alembic's plugin reports
the catalog's copy of an unnamed model constraint as removed.  This comparator pairs each unnamed constraint with a
reflected constraint whose canonical expression is equal, and discards the drop for a pair.  An unnamed constraint with
no pair gets an add under the name PostgreSQL chooses for it, so that the downgrade can drop it.

It is registered as its own plugin (``alembic_pg_autogen.checkconstraints``) so it can be disabled independently of
the function/trigger/view comparator, and only fires for the ``postgresql`` dialect.
"""

from __future__ import annotations

import logging
from itertools import chain
from typing import TYPE_CHECKING

from alembic.operations import ops
from alembic.util import DispatchPriority, PriorityDispatchResult
from sqlalchemy import CheckConstraint

from alembic_pg_autogen.canonicalize import canonicalize_check_constraints, choose_check_constraint_names
from alembic_pg_autogen.inspect import current_schema, inspect_check_constraints
from alembic_pg_autogen.ops import CreateCheckConstraintNotValidOp, ValidateConstraintOp
from alembic_pg_autogen.value_sets import ValueSetChange, compare_value_sets, parse_value_set

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from collections.abc import Set as AbstractSet
    from typing import Final

    from alembic.autogenerate.api import AutogenContext
    from alembic.operations.ops import MigrateOperation, ModifyTableOps
    from alembic.runtime.plugins import Plugin
    from sqlalchemy import Table
    from sqlalchemy.engine import Connection, Dialect

    from alembic_pg_autogen.inspect import CheckConstraintInfo

log = logging.getLogger(__name__)

VALIDATION_MODE_KEY: Final = "pg_check_constraint_validation"
"""The ``context.configure()`` option that selects how a changed value set is validated."""

VALIDATION_MODES: Final = ("deferred", "immediate")
"""Accepted values for :data:`VALIDATION_MODE_KEY`.  The first entry is the default."""

_DEFERRED_CHANGES: Final = frozenset({ValueSetChange.NARROWING, ValueSetChange.DISJOINT})
"""Value set changes that remove values and therefore qualify for a ``NOT VALID`` addition.

A ``WIDENING`` is left out on purpose.  Every existing row already satisfies a superset, so its validation cannot fail,
and one validating statement spares the user a second revision.
"""


def setup(plugin: Plugin) -> None:
    """Register the check constraint expression comparator with Alembic's plugin system."""
    # ``LAST`` runs this comparator after Alembic's own check constraint plugin, whose drop of a type-bound
    # constraint this comparator discards (see ``_discard_drops``).
    plugin.add_autogenerate_comparator(
        _compare_check_constraint_expressions,
        "table",
        "check_constraint_expressions",
        qualifier="postgresql",
        priority=DispatchPriority.LAST,
    )
    log.debug("alembic-pg-autogen check constraint comparator registered")


def _compare_check_constraint_expressions(
    autogen_context: AutogenContext,
    modify_table_ops: ModifyTableOps,
    schema: str | None,
    table_name: str,
    conn_table: Table | None,
    metadata_table: Table | None,
) -> PriorityDispatchResult:
    """Emit operations for check constraints whose expression, validation state, or existence changed."""
    # A table that exists on only one side is handled by Alembic: the constraint travels with the CREATE/DROP TABLE.
    if conn_table is None or metadata_table is None:
        return PriorityDispatchResult.CONTINUE

    conn = autogen_context.connection
    if conn is None:  # offline autogenerate has nothing to normalize against
        return PriorityDispatchResult.CONTINUE

    metadata_constraints = _metadata_check_constraints(metadata_table, autogen_context.dialect)
    unnamed_constraints = _unnamed_check_constraints(metadata_table, autogen_context.dialect)
    if not metadata_constraints and not unnamed_constraints:
        return PriorityDispatchResult.CONTINUE

    defer_validation = _validation_mode(autogen_context.opts) == "deferred"  # pyright: ignore[reportAttributeAccessIssue]

    resolved_schema = schema if schema is not None else current_schema(conn)
    current = {
        info.name: info for info in inspect_check_constraints(conn, schemas=[resolved_schema], table_names=[table_name])
    }

    def name_included(name: str) -> bool:
        return autogen_context.run_name_filters(
            name, "check_constraint", {"table_name": table_name, "schema_name": schema}
        )

    # A type-bound constraint is invisible to Alembic's own plugin, so its existence is ours to manage as well.
    owned = {name for name, constraint in metadata_constraints.items() if getattr(constraint, "_type_bound", False)}
    _discard_drops(modify_table_ops, owned & set(current), table_name)
    for name in sorted(owned - set(current)):
        constraint = metadata_constraints[name]
        if not name_included(name):
            continue
        # A column Alembic is about to add brings its constraint along: ``op.add_column()`` emits every constraint
        # the column's type generates.  Adding it here too would fail the upgrade with a duplicate constraint.
        if any(column.name not in conn_table.c for column in constraint.columns):
            log.debug("Type-bound check constraint %r waits for its column on table %r", name, table_name)
            continue
        if not autogen_context.run_object_filters(constraint, name, "check_constraint", False, None):
            continue
        expression = _compile_check_expression(constraint, autogen_context.dialect)
        if expression is None:
            continue
        log.info("Detected missing type-bound check constraint %r on table %r", name, table_name)
        modify_table_ops.ops.append(_create_check_constraint_op(constraint, table_name, schema, expression))

    if unnamed_constraints:
        # A reflected constraint whose name a named metadata constraint uses is already matched, so it cannot pair.
        pairable = {
            name: info for name, info in current.items() if name not in metadata_constraints and name_included(name)
        }
        _compare_unnamed_check_constraints(
            autogen_context,
            modify_table_ops,
            conn,
            schema=schema,
            resolved_schema=resolved_schema,
            table_name=table_name,
            conn_table=conn_table,
            constraints=unnamed_constraints,
            candidates=pairable,
            name_included=name_included,
        )

    # Only constraints that exist on both sides are ours to check.  Other additions and removals are Alembic's job.
    shared = [name for name in sorted(set(metadata_constraints) & set(current)) if name_included(name)]
    if not shared:
        return PriorityDispatchResult.CONTINUE

    compiled: dict[str, str] = {}
    candidates: dict[str, str] = {}
    for name in shared:
        expression = _compile_check_expression(metadata_constraints[name], autogen_context.dialect)
        if expression is None:
            continue
        compiled[name] = expression
        # An expression that already matches the catalog's deparsed form needs no round-trip.
        if not _same_sql(expression, current[name].expression):
            candidates[name] = expression

    normalized: Mapping[str, str] = {}
    if candidates:
        normalized = canonicalize_check_constraints(
            conn, schema=resolved_schema, table_name=table_name, expressions=candidates
        )

    for name in shared:
        if name not in compiled:
            continue
        info = current[name]
        desired = normalized.get(name) if name in candidates else info.expression
        if desired is None:  # the probe failed; the canonicalizer already logged why
            continue

        metadata_constraint = metadata_constraints[name]
        if desired == info.expression:
            if info.validated or _declares_not_valid(metadata_constraint):
                continue
            conn_constraint = _reflected_constraint(info.expression, name, conn_table, validated=False)
            if not autogen_context.run_object_filters(
                metadata_constraint, name, "check_constraint", False, conn_constraint
            ):
                continue
            log.info(
                "Detected NOT VALID check constraint %r on table %r; emitting VALIDATE CONSTRAINT", name, table_name
            )
            modify_table_ops.ops.append(ValidateConstraintOp(name, table_name, schema=schema))
            continue

        conn_constraint = _reflected_constraint(info.expression, name, conn_table, validated=info.validated)
        if not autogen_context.run_object_filters(
            metadata_constraint, name, "check_constraint", False, conn_constraint
        ):
            continue

        log.info(
            "Detected changed check constraint %r on table %r: %r to %r",
            name,
            table_name,
            info.expression,
            desired,
        )
        modify_table_ops.ops.append(ops.DropConstraintOp.from_constraint(conn_constraint))
        modify_table_ops.ops.append(
            _add_constraint_op(
                metadata_constraint,
                table_name,
                schema,
                compiled[name],
                current_expression=info.expression,
                desired_expression=desired,
                defer_validation=defer_validation,
            )
        )

    return PriorityDispatchResult.CONTINUE


def _compare_unnamed_check_constraints(
    autogen_context: AutogenContext,
    modify_table_ops: ModifyTableOps,
    conn: Connection,
    *,
    schema: str | None,
    resolved_schema: str,
    table_name: str,
    conn_table: Table,
    constraints: Sequence[CheckConstraint],
    candidates: Mapping[str, CheckConstraintInfo],
    name_included: Callable[[str], bool],
) -> None:
    """Pair unnamed metadata constraints with reflected constraints by canonical expression.

    PostgreSQL names every constraint, so Alembic's plugin cannot match an unnamed model constraint and reports the
    catalog's copy as removed. This function pairs each unnamed constraint with a reflected constraint in *candidates*
    whose canonical expression is equal. For a pair, it discards Alembic's drop and applies the validation rule of a
    named pair. An unnamed constraint with no pair gets an add under the name PostgreSQL chooses for it, so that the
    downgrade can drop it. A reflected constraint with no pair keeps the drop that Alembic's plugin emitted.

    An expression can occur several times on each side. The copies pair in metadata order and in the order of
    *candidates*, which :func:`~alembic_pg_autogen.inspect.inspect_check_constraints` sorts by name. A count that
    differs between the two sides is logged as a warning.
    """
    compiled: dict[int, str] = {}
    for index, constraint in enumerate(constraints):
        # A column Alembic is about to add brings a type-bound constraint along, and a probe cannot reference it yet.
        if any(column.name not in conn_table.c for column in constraint.columns):
            log.debug("Unnamed check constraint waits for its column on table %r", table_name)
            continue
        expression = _compile_check_expression(constraint, autogen_context.dialect)
        if expression is not None:
            compiled[index] = expression

    canonical: dict[int, str] = {}
    probes: dict[str, str] = {}
    labels: dict[str, int] = {}
    for index, expression in compiled.items():
        # An expression that already matches a deparsed catalog expression needs no round trip.
        match = next((info.expression for info in candidates.values() if _same_sql(expression, info.expression)), None)
        if match is not None:
            canonical[index] = match
        elif candidates:
            label = f"unnamed check constraint #{index} ({expression})"
            probes[label] = expression
            labels[label] = index
    if probes:
        normalized = canonicalize_check_constraints(
            conn, schema=resolved_schema, table_name=table_name, expressions=probes
        )
        canonical.update({labels[label]: expression for label, expression in normalized.items()})

    reflected_by_expression: dict[str, list[CheckConstraintInfo]] = {}
    for info in candidates.values():
        reflected_by_expression.setdefault(info.expression, []).append(info)
    metadata_by_expression: dict[str, list[int]] = {}
    for index in sorted(canonical):
        metadata_by_expression.setdefault(canonical[index], []).append(index)

    # Without candidates nothing can pair, so every compiled constraint is missing from the catalog. With candidates, a
    # constraint whose probe failed has no canonical expression. It is left alone, because it may match a candidate.
    unpaired = [] if candidates else sorted(compiled)
    paired: list[tuple[CheckConstraint, CheckConstraintInfo]] = []
    for expression, indexes in metadata_by_expression.items():
        reflected = reflected_by_expression.get(expression, [])
        if reflected and len(reflected) != len(indexes):
            log.warning(
                "Check expression %r occurs %d times in the model and %d times on table %r; pairing %d",
                expression,
                len(indexes),
                len(reflected),
                table_name,
                min(len(indexes), len(reflected)),
            )
        paired.extend((constraints[index], info) for index, info in zip(indexes, reflected, strict=False))
        unpaired.extend(indexes[len(reflected) :])
    unpaired.sort()

    _discard_drops(modify_table_ops, {info.name for _, info in paired}, table_name)
    for constraint, info in paired:
        log.debug("Paired unnamed check constraint with %r on table %r", info.name, table_name)
        if info.validated or _declares_not_valid(constraint):
            continue
        conn_constraint = _reflected_constraint(info.expression, info.name, conn_table, validated=False)
        if not autogen_context.run_object_filters(constraint, info.name, "check_constraint", False, conn_constraint):
            continue
        log.info(
            "Detected NOT VALID check constraint %r on table %r; emitting VALIDATE CONSTRAINT", info.name, table_name
        )
        modify_table_ops.ops.append(ValidateConstraintOp(info.name, table_name, schema=schema))

    if not unpaired:
        return
    names = choose_check_constraint_names(
        conn, schema=resolved_schema, table_name=table_name, expressions=[compiled[index] for index in unpaired]
    )
    for index, name in zip(unpaired, names, strict=True):
        constraint = constraints[index]
        if name is None or not name_included(name):
            continue
        if not autogen_context.run_object_filters(constraint, name, "check_constraint", False, None):
            continue
        log.info("Detected missing unnamed check constraint on table %r; adding it as %r", table_name, name)
        modify_table_ops.ops.append(
            _create_check_constraint_op(constraint, table_name, schema, compiled[index], name=name)
        )


def _metadata_check_constraints(table: Table, dialect: Dialect) -> dict[str, CheckConstraint]:
    """Return the table's named check constraints keyed by their final compiled name.

    Constraints declared on a column rather than on the table are included, since SQLAlchemy keeps those in the
    column's own collection.  A type-bound constraint, one SQLAlchemy generates for a type such as
    ``Enum(native_enum=False)`` or ``Boolean(create_constraint=True)``, is included only when SQLAlchemy creates it on
    this dialect (see :func:`_created_on_dialect`).  The catalog holds exactly those, so nothing else can be compared.
    """
    constraints: dict[str, CheckConstraint] = {}
    for constraint in chain(table.constraints, *(column.constraints for column in table.columns)):
        if not isinstance(constraint, CheckConstraint):
            continue
        if constraint.name is None:
            continue
        if getattr(constraint, "_type_bound", False) and not _created_on_dialect(constraint, dialect):
            continue
        # Resolves naming conventions to the name the constraint would actually be created with.
        name = dialect.identifier_preparer.format_constraint(constraint, _alembic_quote=False)
        if not isinstance(name, str) or not name:
            continue
        constraints[name] = constraint
    return constraints


def _unnamed_check_constraints(table: Table, dialect: Dialect) -> list[CheckConstraint]:
    """Return the table's check constraints that have no name, in declaration order.

    A constraint has no name when its name is *None*, or when SQLAlchemy's ``_NONE_NAME`` marker resolves to no name.
    SQLAlchemy gives that marker to the constraint of an ``Enum`` without a ``name``. A constraint that a naming
    convention names has a name and belongs to :func:`_metadata_check_constraints`. The rule for type-bound constraints
    is the same as there: only one that SQLAlchemy creates on this dialect is included.
    """
    constraints: list[CheckConstraint] = []
    for constraint in chain(table.constraints, *(column.constraints for column in table.columns)):
        if not isinstance(constraint, CheckConstraint):
            continue
        if constraint.name is not None and (
            dialect.identifier_preparer.format_constraint(constraint, _alembic_quote=False) is not None
        ):
            continue
        if getattr(constraint, "_type_bound", False) and not _created_on_dialect(constraint, dialect):
            continue
        constraints.append(constraint)
    return constraints


def _discard_drops(modify_table_ops: ModifyTableOps, names: AbstractSet[str], table_name: str) -> None:
    """Remove the ``DropConstraintOp`` Alembic's plugin emits for a constraint the model still declares.

    Alembic's plugin misses two kinds of constraint on its metadata side. Before Alembic 1.19.2 it excludes type-bound
    constraints, such as the one a non-native ``Enum`` derives. It also skips every unnamed constraint. In both cases
    the catalog's copy looks removed to it, although the catalog and the model agree. This comparator runs after that
    plugin and discards the drop.
    """
    if not names:
        return
    kept: list[MigrateOperation] = []
    for op in modify_table_ops.ops:
        if (
            isinstance(op, ops.DropConstraintOp)
            and op.constraint_type in (None, "check")
            and str(op.constraint_name) in names
        ):
            log.info(
                "Discarding drop of check constraint %r on table %r; the model still declares it",
                op.constraint_name,
                table_name,
            )
            continue
        kept.append(op)
    modify_table_ops.ops[:] = kept


def _created_on_dialect(constraint: CheckConstraint, dialect: Dialect) -> bool:
    """Return whether SQLAlchemy emits this type-bound constraint in DDL for *dialect*.

    SQLAlchemy attaches a ``_create_rule`` to each constraint it derives from a type, and its DDL compiler consults the
    rule before rendering the constraint.  On PostgreSQL the rule accepts a non-native ``Enum`` and rejects ``Boolean``
    (native boolean type) and a native ``Enum`` (a ``pg_enum`` type instead).  A constraint without a rule is skipped,
    which was the behavior for every type-bound constraint before rules were consulted.
    """
    rule = getattr(constraint, "_create_rule", None)
    if rule is None:
        return False
    # ``Compiled.__init__`` accepts ``None`` and skips compilation; the rule only reads ``compiler.dialect``.
    compiler = dialect.ddl_compiler(dialect, None)  # pyright: ignore[reportArgumentType]
    return bool(rule(compiler))


def _compile_check_expression(constraint: CheckConstraint, dialect: Dialect) -> str | None:
    """Compile a metadata constraint's expression to PostgreSQL SQL text, or *None* if it cannot be compiled.

    ``include_table=False`` matches SQLAlchemy's own DDL compiler, so a column expression such as the one a non-native
    ``Enum`` generates renders as ``status IN ('a', 'b')`` rather than ``orders.status IN ('a', 'b')``.
    """
    try:
        return str(
            constraint.sqltext.compile(dialect=dialect, compile_kwargs={"literal_binds": True, "include_table": False})
        )
    except Exception:
        log.warning("Could not compile check constraint %r; treating it as unchanged", constraint.name, exc_info=True)
        return None


def _same_sql(left: str, right: str) -> bool:
    """Compare two SQL expressions ignoring insignificant whitespace."""
    return left.split() == right.split()


def _validation_mode(opts: Mapping[str, object]) -> str:
    """Read :data:`VALIDATION_MODE_KEY` from the autogenerate options, defaulting to ``"deferred"``.

    Raises:
        ValueError: If the option holds a value other than the entries of :data:`VALIDATION_MODES`.
    """
    mode = opts.get(VALIDATION_MODE_KEY, VALIDATION_MODES[0])
    if mode not in VALIDATION_MODES:
        raise ValueError(f"{VALIDATION_MODE_KEY} must be one of {VALIDATION_MODES}, got {mode!r}")
    return mode


def _declares_not_valid(constraint: CheckConstraint) -> bool:
    """Return whether the metadata constraint carries ``postgresql_not_valid=True``."""
    return bool(constraint.dialect_options["postgresql"]["not_valid"])


def _reflected_constraint(expression: str, name: str, table: Table, *, validated: bool) -> CheckConstraint:
    """Build the constraint object that stands for the catalog's current constraint in emitted operations."""
    return CheckConstraint(expression, name=name, table=table, postgresql_not_valid=not validated)


def _create_check_constraint_op(
    constraint: CheckConstraint, table_name: str, schema: str | None, expression: str, *, name: str | None = None
) -> ops.CreateCheckConstraintOp:
    """Build Alembic's own ``CreateCheckConstraintOp`` from the compiled expression text.

    ``AddConstraintOp.from_constraint()`` would carry the metadata constraint's bound column expression instead.  When
    Alembic later calls ``to_constraint()`` on such an operation, to render or reverse it, SQLAlchemy auto-attaches the
    new ``CheckConstraint`` to the table those columns belong to, which is the user's metadata table.  A second
    constraint under the same name then shadows the declared one on the next run in the same process.  Text has no
    columns, so nothing attaches, and Alembic renders the same ``create_check_constraint(...)`` call either way.

    A constraint that declares ``postgresql_not_valid=True`` becomes a :class:`CreateCheckConstraintNotValidOp`, because
    Alembic's renderer would otherwise drop the flag and the migration would validate existing rows at once.

    *name* replaces the constraint's own name. An unnamed constraint needs one, because Alembic reverses the add to a
    ``drop_constraint()`` call that cannot run without a name.
    """
    constraint_name = name if name is not None else constraint.name
    if _declares_not_valid(constraint):
        return CreateCheckConstraintNotValidOp(
            constraint_name, table_name, expression, schema=schema, **constraint.dialect_kwargs
        )
    return ops.CreateCheckConstraintOp(
        constraint_name, table_name, expression, schema=schema, **constraint.dialect_kwargs
    )


def _add_constraint_op(
    constraint: CheckConstraint,
    table_name: str,
    schema: str | None,
    expression: str,
    *,
    current_expression: str,
    desired_expression: str,
    defer_validation: bool,
) -> MigrateOperation:
    """Build the operation that adds the changed constraint.

    A value set that loses values in ``"deferred"`` mode, or a constraint that declares ``postgresql_not_valid=True``,
    is added as :class:`CreateCheckConstraintNotValidOp`.  Every other change, a widening included, keeps Alembic's own
    validating ``CreateCheckConstraintOp``.  Both are built from ``expression``, the compiled metadata text, for the reason given in
    :func:`_create_check_constraint_op`.
    """
    current_set = parse_value_set(current_expression)
    desired_set = parse_value_set(desired_expression)
    change = ValueSetChange.UNKNOWN
    if current_set is not None and desired_set is not None:
        change = compare_value_sets(current_set, desired_set)
    removes_values = change in _DEFERRED_CHANGES

    if not _declares_not_valid(constraint) and not (defer_validation and removes_values):
        return _create_check_constraint_op(constraint, table_name, schema, expression)

    column: str | None = None
    removed: list[str] = []
    if removes_values:
        assert current_set is not None and desired_set is not None
        column = desired_set.column
        removed = sorted(current_set.values - desired_set.values)
        log.info(
            "Value set of check constraint %r is %s; adding it NOT VALID (removed values: %r)",
            constraint.name,
            change.value,
            removed,
        )
    return CreateCheckConstraintNotValidOp(
        constraint.name,
        table_name,
        expression,
        schema=schema,
        column=column,
        removed_values=removed,
        **constraint.dialect_kwargs,
    )
