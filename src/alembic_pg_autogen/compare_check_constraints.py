"""Comparator that verifies ``CHECK`` constraint expressions during autogenerate.

Alembic detects when a named ``CHECK`` constraint is added to or removed from your models, but two constraints that
share a name are always presumed equivalent: normalizing an arbitrary SQL expression is not something Alembic can do
in a backend-agnostic way.  This module closes that gap for PostgreSQL by asking PostgreSQL itself.  Each metadata
expression is round-tripped through the server and compared against the catalog's own deparsed form, so a changed
``CHECK`` expression produces a ``DROP CONSTRAINT`` / ``ADD CONSTRAINT`` pair instead of silently drifting.

This comparator complements Alembic's ``alembic.autogenerate.checkconstraint_byname`` rather than replacing it, and
both are needed: that plugin owns names present on only one side (added and removed constraints), while this one owns
names present on both, which is the case it cannot decide.  The two sets are disjoint, so no operation is emitted
twice.

It is registered as its own plugin (``alembic_pg_autogen.checkconstraints``) so it can be disabled independently of
the function/trigger/view comparator, and only fires for the ``postgresql`` dialect.
"""

from __future__ import annotations

import logging
from itertools import chain
from typing import TYPE_CHECKING

from alembic.operations import ops
from alembic.util import PriorityDispatchResult
from sqlalchemy import CheckConstraint

from alembic_pg_autogen.canonicalize import canonicalize_check_constraints
from alembic_pg_autogen.inspect import current_schema, inspect_check_constraints

if TYPE_CHECKING:
    from alembic.autogenerate.api import AutogenContext
    from alembic.operations.ops import ModifyTableOps
    from alembic.runtime.plugins import Plugin
    from sqlalchemy import Table
    from sqlalchemy.engine import Dialect

log = logging.getLogger(__name__)


def setup(plugin: Plugin) -> None:
    """Register the check constraint expression comparator with Alembic's plugin system."""
    plugin.add_autogenerate_comparator(
        _compare_check_constraint_expressions,
        "table",
        "check_constraint_expressions",
        qualifier="postgresql",
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
    """Emit drop/add operations for named check constraints whose expression changed."""
    # A table that exists on only one side is handled by Alembic: the constraint travels with the CREATE/DROP TABLE.
    if conn_table is None or metadata_table is None:
        return PriorityDispatchResult.CONTINUE

    conn = autogen_context.connection
    if conn is None:  # offline autogenerate has nothing to normalize against
        return PriorityDispatchResult.CONTINUE

    metadata_constraints = _metadata_check_constraints(metadata_table, autogen_context.dialect)
    if not metadata_constraints:
        return PriorityDispatchResult.CONTINUE

    resolved_schema = schema if schema is not None else current_schema(conn)
    current = {
        info.name: info for info in inspect_check_constraints(conn, schemas=[resolved_schema], table_names=[table_name])
    }

    # Only constraints that exist on both sides are ours to check.  Additions and removals are Alembic's job.
    shared = [
        name
        for name in sorted(set(metadata_constraints) & set(current))
        if autogen_context.run_name_filters(name, "check_constraint", {"table_name": table_name, "schema_name": schema})
    ]
    if not shared:
        return PriorityDispatchResult.CONTINUE

    candidates: dict[str, str] = {}
    for name in shared:
        expression = _compile_check_expression(metadata_constraints[name], autogen_context.dialect)
        if expression is None:
            continue
        # An expression that already matches the catalog's deparsed form needs no round-trip.
        if _same_sql(expression, current[name].expression):
            continue
        candidates[name] = expression

    if not candidates:
        return PriorityDispatchResult.CONTINUE

    normalized = canonicalize_check_constraints(
        conn, schema=resolved_schema, table_name=table_name, expressions=candidates
    )

    for name in sorted(candidates):
        desired = normalized.get(name)
        if desired is None or desired == current[name].expression:
            continue

        metadata_constraint = metadata_constraints[name]
        conn_constraint = CheckConstraint(current[name].expression, name=name, table=conn_table)
        if not autogen_context.run_object_filters(
            metadata_constraint, name, "check_constraint", False, conn_constraint
        ):
            continue

        log.info(
            "Detected changed check constraint %r on table %r: %r to %r",
            name,
            table_name,
            current[name].expression,
            desired,
        )
        modify_table_ops.ops.append(ops.DropConstraintOp.from_constraint(conn_constraint))
        modify_table_ops.ops.append(ops.AddConstraintOp.from_constraint(metadata_constraint))

    return PriorityDispatchResult.CONTINUE


def _metadata_check_constraints(table: Table, dialect: Dialect) -> dict[str, CheckConstraint]:
    """Return the table's named, non-type-bound check constraints keyed by their final compiled name.

    Constraints declared on a column rather than on the table are included, since SQLAlchemy keeps those in the
    column's own collection.  Type-bound constraints — the ones SQLAlchemy generates for types such as
    ``Enum(native_enum=False)`` — are skipped because their expression is generated rather than authored, matching what
    Alembic's own comparator does.
    """
    constraints: dict[str, CheckConstraint] = {}
    for constraint in chain(table.constraints, *(column.constraints for column in table.columns)):
        if not isinstance(constraint, CheckConstraint):
            continue
        if constraint.name is None or getattr(constraint, "_type_bound", False):
            continue
        # Resolves naming conventions to the name the constraint would actually be created with.
        name = dialect.identifier_preparer.format_constraint(constraint, _alembic_quote=False)
        if not isinstance(name, str) or not name:
            continue
        constraints[name] = constraint
    return constraints


def _compile_check_expression(constraint: CheckConstraint, dialect: Dialect) -> str | None:
    """Compile a metadata constraint's expression to PostgreSQL SQL text, or *None* if it cannot be compiled."""
    try:
        return str(constraint.sqltext.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
    except Exception:
        log.warning("Could not compile check constraint %r; treating it as unchanged", constraint.name, exc_info=True)
        return None


def _same_sql(left: str, right: str) -> bool:
    """Compare two SQL expressions ignoring insignificant whitespace."""
    return left.split() == right.split()
