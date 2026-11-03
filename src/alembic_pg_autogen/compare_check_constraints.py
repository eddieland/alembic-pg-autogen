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
from typing import TYPE_CHECKING, Any

from alembic.operations import ops
from alembic.util import DispatchPriority, PriorityDispatchResult
from sqlalchemy import CheckConstraint

from alembic_pg_autogen.canonicalize import canonicalize_check_constraints, choose_check_constraint_names
from alembic_pg_autogen.inspect import current_schema, inspect_check_constraints
from alembic_pg_autogen.ops import (
    CreateCheckConstraintNotValidOp,
    CreateCheckConstraintTextOp,
    DropCheckConstraintOp,
    ValidateConstraintOp,
)
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
        if metadata_table is not None:
            _warn_about_column_constraints_of_a_new_table(metadata_table, table_name)
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
        pairable = {name: info for name, info in current.items() if name not in metadata_constraints}
        _compare_unnamed_check_constraints(
            autogen_context,
            modify_table_ops,
            conn,
            schema=schema,
            resolved_schema=resolved_schema,
            table_name=table_name,
            conn_table=conn_table,
            metadata_table=metadata_table,
            constraints=unnamed_constraints,
            candidates=pairable,
            name_included=name_included,
            defer_validation=defer_validation,
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
        modify_table_ops.ops.append(
            DropCheckConstraintOp(info.name, table_name, info.expression, schema=schema, validated=info.validated)
        )
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
    metadata_table: Table,
    constraints: Sequence[CheckConstraint],
    candidates: Mapping[str, CheckConstraintInfo],
    name_included: Callable[[str], bool],
    defer_validation: bool,
) -> None:
    """Pair unnamed metadata constraints with reflected constraints by canonical expression.

    PostgreSQL names every constraint, so Alembic's plugin cannot match an unnamed model constraint and reports the
    catalog's copy as removed. This function pairs each unnamed constraint with a reflected constraint in *candidates*
    whose canonical expression is equal. For a pair, it discards Alembic's drop and applies the validation rule of a
    named pair. A reflected constraint with no pair keeps the drop that Alembic's plugin emitted.

    An unnamed constraint with no pair is either a changed value set or a new constraint. A changed value set has an
    unpaired reflected value set on the same column. It gets the drop and add pair of a named change under the
    reflected name, so a narrowing is added ``NOT VALID``. A new constraint gets an add under the name PostgreSQL
    chooses for it, so that the downgrade can drop it.

    *candidates* includes reflected constraints that ``include_name`` excludes. They pair like the others, so no
    duplicate is added for them, but the comparator emits no operation for them.

    An expression can occur several times on each side. The copies pair in metadata order and in the order of
    *candidates*, which :func:`~alembic_pg_autogen.inspect.inspect_check_constraints` sorts by name. A count that
    differs between the two sides is logged as a warning.
    """
    compiled: dict[int, str] = {}
    for index, constraint in enumerate(constraints):
        # A probe cannot reference a column that Alembic is about to add.
        if any(column.name not in conn_table.c for column in constraint.columns):
            if getattr(constraint, "_type_bound", False):
                # ``op.add_column()`` creates the constraint of a type with the column, so nothing is lost.
                log.debug("Unnamed type-bound check constraint waits for its column on table %r", table_name)
            else:
                log.warning(
                    "Unnamed check constraint %r on table %r references a column that this migration adds. The next "
                    "autogenerate run adds the constraint.",
                    _compile_check_expression(constraint, autogen_context.dialect),
                    table_name,
                )
            continue
        expression = _compile_check_expression(constraint, autogen_context.dialect)
        if expression is not None:
            compiled[index] = expression

    canonical = _canonical_expressions(conn, resolved_schema, table_name, compiled, candidates)

    # A validated copy pairs before a ``NOT VALID`` one, so a duplicate that gets dropped is the weaker copy.
    reflected_by_expression: dict[str, list[CheckConstraintInfo]] = {}
    for info in sorted(candidates.values(), key=lambda info: (not info.validated, info.name)):
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

    paired_names = {info.name for _, info in paired}
    leftover = [info for name, info in candidates.items() if name not in paired_names and name_included(name)]
    unknown = sorted(index for index in compiled if index not in canonical) if candidates else []
    for index in unknown:
        log.warning(
            "Could not compare unnamed check constraint %r on table %r, because PostgreSQL rejected it (see the warning "
            "above). If it references a column that this migration adds, the next autogenerate run compares it. "
            "Otherwise fix the expression.",
            compiled[index],
            table_name,
        )
    if unknown and leftover:
        # A constraint whose probe failed may be the model's copy of a leftover constraint. Dropping that constraint
        # would remove a rule the model still declares, so the drop waits for a run that can compare it.
        log.warning(
            "Keeping check constraints %s on table %r, because an unnamed model constraint could not be compared. "
            "They stay until every unnamed model constraint on the table can be compared, so fix a rejected "
            "expression to let them go.",
            sorted(info.name for info in leftover),
            table_name,
        )
        _discard_drops(modify_table_ops, {info.name for info in leftover}, table_name)
        leftover = []

    _discard_drops(modify_table_ops, {name for name in paired_names if name_included(name)}, table_name)
    for constraint, info in paired:
        if not name_included(info.name):
            log.debug("Paired unnamed check constraint with excluded %r on table %r", info.name, table_name)
            continue
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

    changed = _match_value_set_changes(unpaired, canonical, leftover)
    for index, info in changed.items():
        constraint = constraints[index]
        conn_constraint = _reflected_constraint(info.expression, info.name, conn_table, validated=info.validated)
        if not autogen_context.run_object_filters(constraint, info.name, "check_constraint", False, conn_constraint):
            continue
        log.info(
            "Detected changed unnamed check constraint %r on table %r: %r to %r",
            info.name,
            table_name,
            info.expression,
            canonical[index],
        )
        _discard_drops(modify_table_ops, {info.name}, table_name)
        modify_table_ops.ops.append(
            DropCheckConstraintOp(info.name, table_name, info.expression, schema=schema, validated=info.validated)
        )
        modify_table_ops.ops.append(
            _add_constraint_op(
                constraint,
                table_name,
                schema,
                compiled[index],
                current_expression=info.expression,
                desired_expression=canonical[index],
                defer_validation=defer_validation,
                name=info.name,
            )
        )
    unpaired = [index for index in unpaired if index not in changed]

    if not unpaired:
        return
    names = choose_check_constraint_names(
        conn,
        schema=resolved_schema,
        table_name=table_name,
        expressions=[compiled[index] for index in unpaired],
        reserved=_planned_constraint_names(metadata_table, autogen_context.dialect),
    )
    for index, name in zip(unpaired, names, strict=True):
        constraint = constraints[index]
        if name is None:
            log.warning(
                "Could not add unnamed check constraint %r on table %r, because PostgreSQL rejected it (see the warning "
                "above). If it references a column that this migration adds, the next autogenerate run adds it. "
                "Otherwise fix the expression.",
                compiled[index],
                table_name,
            )
            continue
        if not name_included(name):
            continue
        if not autogen_context.run_object_filters(constraint, name, "check_constraint", False, None):
            continue
        log.info("Detected missing unnamed check constraint on table %r; adding it as %r", table_name, name)
        modify_table_ops.ops.append(
            _create_check_constraint_op(constraint, table_name, schema, compiled[index], name=name)
        )


def _canonical_expressions(
    conn: Connection,
    schema: str,
    table_name: str,
    compiled: Mapping[int, str],
    candidates: Mapping[str, CheckConstraintInfo],
) -> dict[int, str]:
    """Return the canonical form of each compiled unnamed expression, keyed by its position.

    An expression that already matches a deparsed candidate expression needs no round trip. Without candidates, nothing
    can pair, so no expression is canonicalized. An expression whose probe fails is absent from the result.
    """
    canonical: dict[int, str] = {}
    probes: dict[str, str] = {}
    labels: dict[str, int] = {}
    for index, expression in compiled.items():
        match = next((info.expression for info in candidates.values() if _same_sql(expression, info.expression)), None)
        if match is not None:
            canonical[index] = match
        elif candidates:
            label = f"unnamed check constraint #{index} ({expression})"
            probes[label] = expression
            labels[label] = index
    if probes:
        normalized = canonicalize_check_constraints(conn, schema=schema, table_name=table_name, expressions=probes)
        canonical.update({labels[label]: expression for label, expression in normalized.items()})
    return canonical


def _match_value_set_changes(
    unpaired: Sequence[int], canonical: Mapping[int, str], leftover: Sequence[CheckConstraintInfo]
) -> dict[int, CheckConstraintInfo]:
    """Match each unpaired model value set with a leftover reflected value set on the same column.

    The constraint that an unnamed non-native ``Enum`` derives has no name to match by. A changed member list
    therefore leaves one unpaired constraint on each side. Both sides restrict the same column to a set of values, so
    the two are one changed constraint. Each column matches at most once, in the order of *unpaired* and *leftover*.
    """
    reflected_by_column: dict[str, list[CheckConstraintInfo]] = {}
    for info in leftover:
        value_set = parse_value_set(info.expression)
        if value_set is not None:
            reflected_by_column.setdefault(value_set.column, []).append(info)
    changed: dict[int, CheckConstraintInfo] = {}
    for index in unpaired:
        desired = canonical.get(index)
        value_set = parse_value_set(desired) if desired is not None else None
        if value_set is None or not reflected_by_column.get(value_set.column):
            continue
        changed[index] = reflected_by_column[value_set.column].pop(0)
    return changed


def _planned_constraint_names(table: Table, dialect: Dialect) -> set[str]:
    """Return the resolved names of every named constraint on the metadata table.

    Alembic adds the named constraints that the catalog lacks in the same migration, before the unnamed ones. An
    unnamed constraint must not take one of those names.
    """
    names: set[str] = set()
    for constraint in chain(table.constraints, *(column.constraints for column in table.columns)):
        if constraint.name is None:
            continue
        name = dialect.identifier_preparer.format_constraint(constraint, _alembic_quote=False)
        if isinstance(name, str) and name:
            names.add(name)
    return names


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


def _warn_about_column_constraints_of_a_new_table(table: Table, table_name: str) -> None:
    """Warn about unnamed column-level constraints that Alembic's ``create_table`` leaves out.

    Alembic renders a new table's columns without their column-level ``CheckConstraint``. A named one gets its add from
    Alembic's plugin on the next run, and an unnamed one gets its add from this comparator on the next run. A
    constraint that a type generates is not affected, because the column's type creates it.
    """
    for column in table.columns:
        for constraint in column.constraints:
            if (
                isinstance(constraint, CheckConstraint)
                and constraint.name is None
                and not getattr(constraint, "_type_bound", False)
            ):
                log.warning(
                    "New table %r: Alembic's create_table does not create the unnamed check constraint on column %r. "
                    "The next autogenerate run adds it.",
                    table_name,
                    column.name,
                )


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
    # ``Table.constraints`` is a set, so its iteration order varies between processes.
    return sorted(constraints, key=lambda constraint: getattr(constraint, "_creation_order", 0))


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

    A dialect whose driver uses the ``format`` or ``pyformat`` parameter style doubles every ``%`` in compiled SQL,
    including ``%`` in string literals and the modulo operator. The result is SQL text, not a driver statement, so the
    doubling is undone. :func:`~sqlalchemy.text` doubles it again when a probe runs, and Alembic does the same when the
    migration runs.
    """
    try:
        compiled = str(
            constraint.sqltext.compile(dialect=dialect, compile_kwargs={"literal_binds": True, "include_table": False})
        )
    except Exception:
        log.warning("Could not compile check constraint %r; treating it as unchanged", constraint.name, exc_info=True)
        return None
    if getattr(dialect.identifier_preparer, "_double_percents", False):
        compiled = compiled.replace("%%", "%")
    return compiled


def _same_sql(left: str, right: str) -> bool:
    """Compare two SQL expressions, ignoring whitespace outside quoted literals and identifiers.

    Whitespace inside ``'...'`` or ``"..."`` is part of the value, so ``'x  y'`` and ``'x y'`` differ. Text with a
    backslash may hold an ``E'...'`` escape that hides a quote, so it compares exactly.
    """
    if "\\" in left or "\\" in right:
        return left == right
    return _normalize_whitespace(left) == _normalize_whitespace(right)


def _normalize_whitespace(sql: str) -> str:
    """Collapse each run of whitespace outside quotes to one space, and strip the ends."""
    parts: list[str] = []
    quote: str | None = None
    pending_space = False
    for char in sql:
        if quote is None and char.isspace():
            pending_space = True
            continue
        if pending_space and parts:
            parts.append(" ")
        pending_space = False
        parts.append(char)
        if quote is None and char in "'\"":
            quote = char
        elif char == quote:
            quote = None
    return "".join(parts)


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


def _dialect_kwargs(constraint: CheckConstraint) -> dict[str, Any]:
    """Return the constraint's dialect keywords for an add operation, without ``postgresql_not_valid``.

    Reading ``dialect_options`` fills in the default of each PostgreSQL keyword, so ``dialect_kwargs`` then holds
    ``postgresql_not_valid=False``. Alembic 1.20 renders every keyword it receives. The flag is the choice between
    :class:`CreateCheckConstraintTextOp` and :class:`CreateCheckConstraintNotValidOp`, so the operation does not carry
    it.
    """
    return {key: value for key, value in constraint.dialect_kwargs.items() if key != "postgresql_not_valid"}


def _reflected_constraint(expression: str, name: str, table: Table, *, validated: bool) -> CheckConstraint:
    """Build the constraint object that stands for the catalog's current constraint in the object filters."""
    if validated:
        return CheckConstraint(expression, name=name, table=table)
    return CheckConstraint(expression, name=name, table=table, postgresql_not_valid=True)


def _create_check_constraint_op(
    constraint: CheckConstraint, table_name: str, schema: str | None, expression: str, *, name: str | None = None
) -> CreateCheckConstraintTextOp:
    """Build a :class:`CreateCheckConstraintTextOp` from the compiled expression text.

    ``AddConstraintOp.from_constraint()`` would carry the metadata constraint's bound column expression instead.  When
    Alembic later calls ``to_constraint()`` on such an operation, to render or reverse it, SQLAlchemy auto-attaches the
    new ``CheckConstraint`` to the table those columns belong to, which is the user's metadata table.  A second
    constraint under the same name then shadows the declared one on the next run in the same process.  Text has no
    columns, so nothing attaches.  The subclass renders the text exactly, so a ``%`` or a ``:`` in it survives the
    migration.

    A constraint that declares ``postgresql_not_valid=True`` becomes a :class:`CreateCheckConstraintNotValidOp`, because
    Alembic's renderer would otherwise drop the flag and the migration would validate existing rows at once.

    *name* replaces the constraint's own name. An unnamed constraint needs one, because Alembic reverses the add to a
    ``drop_constraint()`` call that cannot run without a name.
    """
    constraint_name = name if name is not None else constraint.name
    if _declares_not_valid(constraint):
        return CreateCheckConstraintNotValidOp(
            constraint_name, table_name, expression, schema=schema, **_dialect_kwargs(constraint)
        )
    return CreateCheckConstraintTextOp(
        constraint_name, table_name, expression, schema=schema, **_dialect_kwargs(constraint)
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
    name: str | None = None,
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
    constraint_name = name if name is not None else constraint.name

    if not _declares_not_valid(constraint) and not (defer_validation and removes_values):
        return _create_check_constraint_op(constraint, table_name, schema, expression, name=name)

    column: str | None = None
    removed: list[str] = []
    if removes_values:
        assert current_set is not None and desired_set is not None
        column = desired_set.column
        removed = sorted(current_set.values - desired_set.values)
        log.info(
            "Value set of check constraint %r is %s; adding it NOT VALID (removed values: %r)",
            constraint_name,
            change.value,
            removed,
        )
    return CreateCheckConstraintNotValidOp(
        constraint_name,
        table_name,
        expression,
        schema=schema,
        column=column,
        removed_values=removed,
        **_dialect_kwargs(constraint),
    )
