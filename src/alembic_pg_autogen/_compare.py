"""Comparator functions for detecting PostgreSQL object diffs during autogenerate."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from alembic.runtime.plugins import Plugin
from alembic.util import PriorityDispatchResult
from sqlalchemy import text

from alembic_pg_autogen._canonicalize import CanonicalState, canonicalize
from alembic_pg_autogen._diff import Action, diff
from alembic_pg_autogen._inspect import inspect_functions, inspect_triggers
from alembic_pg_autogen._ops import (
    CreateFunctionOp,
    CreateTriggerOp,
    DropFunctionOp,
    DropTriggerOp,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from alembic.autogenerate.api import AutogenContext
    from alembic.operations.ops import MigrateOperation, UpgradeOps

    from alembic_pg_autogen._diff import FunctionOp, TriggerOp

log = logging.getLogger(__name__)

#: Extract ``(schema, name)`` from a ``CREATE [OR REPLACE] FUNCTION`` statement.
#:
#: **Why regex is acceptable here.** The canonicalization layer honours the project's
#: "no SQL parsing" rule — it passes DDL verbatim to PostgreSQL and never inspects
#: the strings.  However, ``canonicalize()`` intentionally returns the *full* catalog
#: state for every schema it touches, not just the objects the user declared (see the
#: canonicalization design doc for why snapshot-diff was rejected).  The comparator
#: therefore needs a way to narrow that full catalog back to user-declared objects.
#: Extracting object identities from the DDL is the only way to do that without
#: burdening the public API with redundant ``(schema, name)`` declarations.
#:
#: The patterns are intentionally minimal — they match only the ``CREATE ... name``
#: preamble and ignore the rest of the statement.  They will *not* handle every legal
#: PostgreSQL DDL form (e.g. quoted identifiers, comments before the object name), but
#: they cover the practical subset that this library's users are expected to provide.
#: If robustness becomes an issue, the preferred fix is to push identity resolution
#: into the database (e.g. parse inside a PL/pgSQL helper) rather than expanding
#: the regex.
_FUNCTION_RE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:(\w+)\.)?(\w+)", re.IGNORECASE)

#: Extract ``(trigger_name, schema, table_name)`` from a ``CREATE [OR REPLACE] TRIGGER``
#: statement.  See :data:`_FUNCTION_RE` for the rationale on why lightweight regex
#: extraction is used here.
_TRIGGER_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+(\w+)\s+.*?ON\s+(?:(\w+)\.)?(\w+)", re.IGNORECASE | re.DOTALL
)


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

    pg_functions: Sequence[str] = opts.get("pg_functions", ())
    pg_triggers: Sequence[str] = opts.get("pg_triggers", ())

    conn = autogen_context.connection
    assert conn is not None  # guaranteed during online autogenerate

    resolved_schemas = _resolve_schemas(conn, schemas)
    log.debug("resolved_schemas=%r", resolved_schemas)

    current_functions = inspect_functions(conn, resolved_schemas)
    current_triggers = inspect_triggers(conn, resolved_schemas)
    current = CanonicalState(functions=current_functions, triggers=current_triggers)
    log.debug("current: %d functions, %d triggers", len(current_functions), len(current_triggers))

    canonical = canonicalize(conn, function_ddl=pg_functions, trigger_ddl=pg_triggers)
    canonical = _filter_to_schemas(canonical, resolved_schemas)
    desired = _filter_to_declared(canonical, pg_functions, pg_triggers, conn)
    log.debug("desired: %d functions, %d triggers", len(desired.functions), len(desired.triggers))

    result = diff(current, desired)

    ops = _order_ops(result.function_ops, result.trigger_ops)
    log.debug("generated %d ops: %r", len(ops), [type(o).__name__ for o in ops])
    upgrade_ops.ops.extend(ops)

    return PriorityDispatchResult.CONTINUE


def _filter_to_declared(
    canonical: CanonicalState,
    pg_functions: Sequence[str],
    pg_triggers: Sequence[str],
    conn: object,
) -> CanonicalState:
    """Filter canonical state to only include objects declared in user DDL.

    ``canonicalize()`` returns a full catalog snapshot after executing DDL,
    which includes pre-existing objects.  This function parses identity info
    from the raw DDL strings and keeps only those canonical entries that match.
    """
    fn_names = _parse_function_names(pg_functions, conn)
    trg_ids = _parse_trigger_identities(pg_triggers, conn)

    return CanonicalState(
        functions=[f for f in canonical.functions if (f.schema, f.name) in fn_names],
        triggers=[t for t in canonical.triggers if (t.schema, t.table_name, t.trigger_name) in trg_ids],
    )


def _parse_function_names(ddl_list: Sequence[str], conn: object) -> set[tuple[str, str]]:
    """Extract ``(schema, name)`` pairs from function DDL strings."""
    default_schema = _get_default_schema(conn)
    names: set[tuple[str, str]] = set()
    for ddl in ddl_list:
        m = _FUNCTION_RE.search(ddl)
        if m:
            schema = m.group(1) or default_schema
            names.add((schema, m.group(2)))
    return names


def _parse_trigger_identities(ddl_list: Sequence[str], conn: object) -> set[tuple[str, str, str]]:
    """Extract ``(schema, table_name, trigger_name)`` triples from trigger DDL strings."""
    default_schema = _get_default_schema(conn)
    identities: set[tuple[str, str, str]] = set()
    for ddl in ddl_list:
        m = _TRIGGER_RE.search(ddl)
        if m:
            trigger_name = m.group(1)
            schema = m.group(2) or default_schema
            table_name = m.group(3)
            identities.add((schema, table_name, trigger_name))
    return identities


def _get_default_schema(conn: object) -> str:
    """Get the current schema for the connection."""
    from sqlalchemy import Connection

    assert isinstance(conn, Connection)
    row = conn.execute(text("SELECT current_schema()")).scalar()
    assert row is not None
    return row


def _resolve_schemas(conn: object, schemas: set[str | None]) -> list[str] | None:
    """Convert Alembic's schema set to a list suitable for inspect functions.

    Alembic passes ``{None}`` to mean "only the default schema".  This function
    resolves ``None`` to the connection's ``current_schema()`` so inspect
    functions receive concrete schema names.  If the resulting set covers all
    user schemas, returns ``None`` (meaning "no filter").
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


def _filter_to_schemas(state: CanonicalState, schemas: list[str] | None) -> CanonicalState:
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

    Order: drop triggers, drop functions, create/replace functions,
    create/replace triggers.
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
