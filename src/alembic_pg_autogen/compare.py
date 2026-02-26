"""Comparator functions for detecting PostgreSQL object diffs during autogenerate."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from alembic.runtime.plugins import Plugin
from alembic.util import PriorityDispatchResult
from sqlalchemy import Connection, text

from alembic_pg_autogen.canonicalize import CanonicalState, canonicalize
from alembic_pg_autogen.diff import Action, diff
from alembic_pg_autogen.inspect import inspect_functions, inspect_triggers
from alembic_pg_autogen.ops import (
    CreateFunctionOp,
    CreateTriggerOp,
    DropFunctionOp,
    DropTriggerOp,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from alembic.autogenerate.api import AutogenContext
    from alembic.operations.ops import MigrateOperation, UpgradeOps

    from alembic_pg_autogen.diff import FunctionOp, TriggerOp

log = logging.getLogger(__name__)


class _HasText(Protocol):
    """Protocol for objects with a ``.text`` string attribute (e.g. SQLAlchemy ``TextClause``)."""

    @property
    def text(self) -> str: ...


class SQLCreatable(Protocol):
    """Protocol for alembic-utils-style objects that produce DDL via ``to_sql_statement_create()``.

    Any object implementing this method (e.g. ``PGFunction``, ``PGView``, ``PGTrigger`` from alembic-utils) can be
    passed wherever this library expects DDL strings.  The DDL is extracted by calling
    ``obj.to_sql_statement_create().text``.
    """

    def to_sql_statement_create(self) -> _HasText:  # noqa: D102
        ...


def setup(plugin: Plugin) -> None:
    """Register the PostgreSQL object comparator with Alembic's plugin system."""
    plugin.add_autogenerate_comparator(_compare_pg_objects, "schema")
    log.debug("alembic-pg-autogen comparator registered")


def _compare_pg_objects(
    autogen_context: AutogenContext,
    upgrade_ops: UpgradeOps,
    schemas: set[str | None],
) -> PriorityDispatchResult:
    """Compare current database state against desired functions/triggers."""
    opts = autogen_context.opts  # pyright: ignore[reportAttributeAccessIssue]
    log.debug(
        "_compare_pg_objects called, schemas=%r, pg_functions in opts=%r, pg_triggers in opts=%r",
        schemas,
        "pg_functions" in opts,
        "pg_triggers" in opts,
    )
    if "pg_functions" not in opts and "pg_triggers" not in opts:
        log.debug("Neither pg_functions nor pg_triggers in opts, skipping")
        return PriorityDispatchResult.CONTINUE

    pg_functions = _resolve_ddl(opts.get("pg_functions", ()))
    pg_triggers = _resolve_ddl(opts.get("pg_triggers", ()))

    conn = autogen_context.connection
    assert conn is not None  # guaranteed during online autogenerate

    resolved_schemas = _resolve_schemas(conn, schemas)
    log.debug("resolved_schemas=%r", resolved_schemas)

    current_functions = inspect_functions(conn, resolved_schemas)
    current_triggers = inspect_triggers(conn, resolved_schemas)
    current = CanonicalState(functions=current_functions, triggers=current_triggers)
    log.info("Found %d functions and %d triggers in database", len(current_functions), len(current_triggers))

    canonical = canonicalize(conn, function_ddl=pg_functions, trigger_ddl=pg_triggers)
    canonical = _filter_to_schemas(canonical, resolved_schemas)
    desired = _filter_to_declared(canonical, pg_functions, pg_triggers, conn)
    log.debug("desired: %d functions, %d triggers", len(desired.functions), len(desired.triggers))

    result = diff(current, desired)

    ops = _order_ops(result.function_ops, result.trigger_ops)
    log.info("Autogenerate produced %d migration ops: %r", len(ops), [type(o).__name__ for o in ops])
    upgrade_ops.ops.extend(ops)

    return PriorityDispatchResult.CONTINUE


def _resolve_ddl(items: Sequence[str | SQLCreatable]) -> tuple[str, ...]:
    """Convert a mixed sequence of DDL strings and ``SQLCreatable`` objects to plain DDL strings.

    Strings are passed through unchanged.  ``SQLCreatable`` objects (e.g. alembic-utils entities) are converted by
    calling ``obj.to_sql_statement_create().text``.
    """
    return tuple(item if isinstance(item, str) else item.to_sql_statement_create().text for item in items)


def _filter_to_declared(
    canonical: CanonicalState,
    pg_functions: Sequence[str],
    pg_triggers: Sequence[str],
    conn: Connection,
) -> CanonicalState:
    """Filter canonical state to only include objects declared in user DDL.

    ``canonicalize()`` returns a full catalog snapshot after executing DDL, which includes pre-existing objects.  This
    function parses identity info from the raw DDL strings and keeps only those canonical entries that match.
    """
    fn_names = _parse_function_names(pg_functions, conn)
    trg_ids = _parse_trigger_identities(pg_triggers, conn)

    functions = [f for f in canonical.functions if (f.schema, f.name) in fn_names]
    triggers = [t for t in canonical.triggers if (t.schema, t.table_name, t.trigger_name) in trg_ids]

    fn_dropped = len(canonical.functions) - len(functions)
    trg_dropped = len(canonical.triggers) - len(triggers)
    if fn_dropped or trg_dropped:
        log.debug("Filtered out %d functions and %d triggers not in user DDL", fn_dropped, trg_dropped)

    if pg_functions and not functions:
        log.warning("No canonical functions matched user DDL — check schema qualifiers in pg_functions")
    if pg_triggers and not triggers:
        log.warning("No canonical triggers matched user DDL — check schema qualifiers in pg_triggers")

    return CanonicalState(functions=functions, triggers=triggers)


def _parse_function_names(ddl_list: Sequence[str], conn: Connection) -> set[tuple[str, str]]:
    """Extract ``(schema, name)`` pairs from function DDL strings via postgast.

    Raises:
        ValueError: If any DDL string does not contain a valid ``CREATE FUNCTION`` statement.
    """
    import postgast

    default_schema = _get_default_schema(conn)
    names: set[tuple[str, str]] = set()
    for ddl in ddl_list:
        identity = postgast.extract_function_identity(postgast.parse(ddl))
        if identity is None:
            raise ValueError(f"Cannot parse function identity from pg_functions DDL: {ddl!r}")
        schema = identity.schema if identity.schema is not None else default_schema
        names.add((schema, identity.name))
    return names


def _parse_trigger_identities(ddl_list: Sequence[str], conn: Connection) -> set[tuple[str, str, str]]:
    """Extract ``(schema, table_name, trigger_name)`` triples from trigger DDL strings via postgast.

    Raises:
        ValueError: If any DDL string does not contain a valid ``CREATE TRIGGER`` statement.
    """
    import postgast

    default_schema = _get_default_schema(conn)
    identities: set[tuple[str, str, str]] = set()
    for ddl in ddl_list:
        identity = postgast.extract_trigger_identity(postgast.parse(ddl))
        if identity is None:
            raise ValueError(f"Cannot parse trigger identity from pg_triggers DDL: {ddl!r}")
        schema = identity.schema if identity.schema is not None else default_schema
        identities.add((schema, identity.table, identity.trigger))
    return identities


def _get_default_schema(conn: Connection) -> str:
    """Get the current schema for the connection."""
    row = conn.execute(text("SELECT current_schema()")).scalar()
    assert row is not None, "Failed to read current_schema()"
    return row


def _resolve_schemas(conn: Connection, schemas: Iterable[str | None]) -> list[str] | None:
    """Convert Alembic's schema set to a list suitable for inspect functions.

    Alembic passes ``{None}`` to mean "only the default schema".  This function resolves ``None`` to the connection's
    ``current_schema()`` so inspect functions receive concrete schema names.  If the resulting set covers all user
    schemas, returns ``None`` (meaning "no filter").
    """
    if not schemas:
        return None

    resolved: list[str] = []
    for s in schemas:
        if s is None:
            resolved.append(_get_default_schema(conn))
        else:
            resolved.append(s)
    return resolved


def _filter_to_schemas(state: CanonicalState, schemas: Iterable[str] | None) -> CanonicalState:
    """Filter a CanonicalState to only include objects in the given schemas."""
    if schemas is None:
        return state
    schema_set = set(schemas)
    return CanonicalState(
        functions=[f for f in state.functions if f.schema in schema_set],
        triggers=[t for t in state.triggers if t.schema in schema_set],
    )


def _order_ops(
    function_ops: Sequence[FunctionOp],
    trigger_ops: Sequence[TriggerOp],
) -> list[MigrateOperation]:
    """Convert diff ops to MigrateOperation instances in dependency-safe order.

    Order: drop triggers, drop functions, create/replace functions, create/replace triggers.
    """
    result: list[MigrateOperation] = []

    # 1. Drop triggers first (frees functions for removal)
    for op in trigger_ops:
        if op.action is Action.DROP:
            assert op.current is not None
            result.append(DropTriggerOp(op.current))

    # 2. Drop functions
    for op in function_ops:
        if op.action is Action.DROP:
            assert op.current is not None
            result.append(DropFunctionOp(op.current))

    # 3. Create/replace functions (must exist before triggers reference them)
    for op in function_ops:
        if op.action is Action.CREATE:
            assert op.desired is not None
            result.append(CreateFunctionOp(op.desired))
        elif op.action is Action.REPLACE:
            assert op.current is not None and op.desired is not None
            result.append(ReplaceFunctionOp(op.current, op.desired))

    # 4. Create/replace triggers
    for op in trigger_ops:
        if op.action is Action.CREATE:
            assert op.desired is not None
            result.append(CreateTriggerOp(op.desired))
        elif op.action is Action.REPLACE:
            assert op.current is not None and op.desired is not None
            result.append(ReplaceTriggerOp(op.current, op.desired))

    return result
