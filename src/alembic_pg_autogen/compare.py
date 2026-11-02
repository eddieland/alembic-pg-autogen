"""Comparator functions for detecting PostgreSQL object diffs during autogenerate."""

from __future__ import annotations

import difflib
import itertools
import logging
from typing import TYPE_CHECKING, Protocol

from alembic.runtime.plugins import Plugin
from alembic.util import PriorityDispatchResult
from sqlalchemy import Connection

from alembic_pg_autogen.canonicalize import CanonicalState, canonicalize, canonicalize_grants
from alembic_pg_autogen.diff import Action, diff
from alembic_pg_autogen.inspect import (
    current_schema,
    inspect_functions,
    inspect_grants,
    inspect_triggers,
    inspect_views,
)
from alembic_pg_autogen.ops import (
    AlterGrantOptionOp,
    CreateFunctionOp,
    CreateTriggerOp,
    CreateViewOp,
    DropFunctionOp,
    DropTriggerOp,
    DropViewOp,
    GrantPrivilegesOp,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
    ReplaceViewOp,
    RevokePrivilegesOp,
)
from alembic_pg_autogen.sentinels import IGNORED

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from typing import Final

    from alembic.autogenerate.api import AutogenContext
    from alembic.operations.ops import MigrateOperation, UpgradeOps

    from alembic_pg_autogen.diff import FunctionOp, GrantOp, TriggerOp, ViewOp
    from alembic_pg_autogen.inspect import FunctionInfo, GrantInfo, TriggerInfo, ViewInfo
    from alembic_pg_autogen.sentinels import Ignored

log = logging.getLogger(__name__)

_DESIRED_STATE_KEYS: Final = ("pg_functions", "pg_triggers", "pg_views", "pg_grants")
"""Configuration keys this comparator reads from ``autogen_context.opts``."""

_RECOGNIZED_KEYS: Final = (*_DESIRED_STATE_KEYS, "pg_check_constraint_validation")
"""Every ``pg_*`` option this package reads, including the check constraint comparator's validation mode."""

_TYPO_CUTOFF: Final = 0.8
"""Similarity above which an unrecognized ``pg_*`` option is reported as a probable misspelling."""


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
    """Compare current database state against desired functions, triggers, views, and grants."""
    opts = autogen_context.opts  # pyright: ignore[reportAttributeAccessIssue]
    log.debug(
        "_compare_pg_objects called, schemas=%r, declared keys=%r",
        schemas,
        [key for key in _DESIRED_STATE_KEYS if key in opts],
    )
    _warn_unrecognized_options(opts)

    pg_functions = _resolve_ddl(opts.get("pg_functions", IGNORED))
    pg_triggers = _resolve_ddl(opts.get("pg_triggers", IGNORED))
    pg_views = _resolve_ddl(opts.get("pg_views", IGNORED))
    pg_grants = _resolve_ddl(opts.get("pg_grants", IGNORED))

    declared_by_label = {
        "functions": pg_functions,
        "triggers": pg_triggers,
        "views": pg_views,
        "grants": pg_grants,
    }
    unmanaged = [label for label, declared in declared_by_label.items() if declared is IGNORED]
    if len(unmanaged) == len(declared_by_label):
        log.debug("No object types are managed, skipping")
        return PriorityDispatchResult.CONTINUE
    if unmanaged:
        log.info("PostgreSQL object types left unmanaged: %s", ", ".join(unmanaged))

    conn = autogen_context.connection
    assert conn is not None  # guaranteed during online autogenerate

    resolved_schemas = _resolve_schemas(conn, schemas)
    log.debug("resolved_schemas=%r", resolved_schemas)

    current_functions: Sequence[FunctionInfo] = ()
    current_triggers: Sequence[TriggerInfo] = ()
    current_views: Sequence[ViewInfo] = ()
    current_grants: Sequence[GrantInfo] = ()
    desired_grants: Sequence[GrantInfo] = ()
    if pg_functions is not IGNORED:
        current_functions = inspect_functions(conn, resolved_schemas)
    if pg_triggers is not IGNORED:
        current_triggers = inspect_triggers(conn, resolved_schemas)
    if pg_views is not IGNORED:
        current_views = inspect_views(conn, resolved_schemas)
    if pg_grants is not IGNORED:
        current_grants, desired_grants = _grant_states(conn, pg_grants, resolved_schemas)
    current = CanonicalState(
        functions=current_functions, triggers=current_triggers, views=current_views, grants=current_grants
    )
    log.info(
        "Found %d functions, %d triggers, %d views, and %d grants in database",
        len(current_functions),
        len(current_triggers),
        len(current_views),
        len(current_grants),
    )

    canonical = canonicalize(conn, function_ddl=pg_functions, view_ddl=pg_views, trigger_ddl=pg_triggers)
    canonical = _filter_to_schemas(canonical, resolved_schemas)
    desired = _filter_to_declared(canonical, pg_functions, pg_triggers, pg_views, conn)._replace(grants=desired_grants)
    log.debug(
        "desired: %d functions, %d triggers, %d views, %d grants",
        len(desired.functions),
        len(desired.triggers),
        len(desired.views),
        len(desired.grants),
    )

    result = diff(current, desired)

    revoke_ops, grant_ops = _grant_migration_ops(result.grant_ops)
    ops = _order_ops(result.function_ops, result.trigger_ops, result.view_ops)
    log.info(
        "Autogenerate produced %d migration ops: %r",
        len(revoke_ops) + len(ops) + len(grant_ops),
        [type(o).__name__ for o in (*revoke_ops, *ops, *grant_ops)],
    )
    # Revokes go before every other operation, including the table operations of Alembic, which ran first. A REVOKE
    # after the DROP TABLE of its table would fail. Grants go last, after the CREATE TABLE and CREATE VIEW of their
    # object. ``downgrade()`` reverses the list, so the same rules hold there.
    upgrade_ops.ops[:0] = revoke_ops
    upgrade_ops.ops.extend(ops)
    upgrade_ops.ops.extend(grant_ops)

    return PriorityDispatchResult.CONTINUE


def _warn_unrecognized_options(opts: Mapping[str, object]) -> None:
    """Warn about ``pg_*`` options that look like a misspelled desired-state key.

    An absent key leaves its object type unmanaged, so a misspelling such as ``pg_view`` would otherwise silently
    disable management of that type.  Only close matches are reported: a ``pg_*`` option belonging to another plugin is
    not a typo and is left alone.
    """
    for key in opts:
        if key in _RECOGNIZED_KEYS or not key.startswith("pg_"):
            continue
        matches = difflib.get_close_matches(key, _RECOGNIZED_KEYS, n=1, cutoff=_TYPO_CUTOFF)
        if matches:
            log.warning("Unrecognized autogenerate option %r — did you mean %r?", key, matches[0])


def _resolve_ddl(items: Sequence[str | SQLCreatable] | Ignored) -> tuple[str, ...] | Ignored:
    """Convert a mixed sequence of DDL strings and ``SQLCreatable`` objects to plain DDL strings.

    Strings are passed through unchanged.  ``SQLCreatable`` objects (e.g. alembic-utils entities) are converted by
    calling ``obj.to_sql_statement_create().text``.  :data:`~alembic_pg_autogen.IGNORED` is passed through unchanged.
    """
    if items is IGNORED:
        return IGNORED
    return tuple(item if isinstance(item, str) else item.to_sql_statement_create().text for item in items)


def _filter_to_declared(
    canonical: CanonicalState,
    pg_functions: Sequence[str] | Ignored,
    pg_triggers: Sequence[str] | Ignored,
    pg_views: Sequence[str] | Ignored,
    conn: Connection,
) -> CanonicalState:
    """Filter canonical state to only include objects declared in user DDL.

    ``canonicalize()`` returns a full catalog snapshot after executing DDL, which includes pre-existing objects.  This
    function parses identity info from the raw DDL strings and keeps only those canonical entries that match.  Object
    types marked :data:`~alembic_pg_autogen.IGNORED` yield an empty desired set without parsing any DDL.
    """
    functions: Sequence[FunctionInfo] = ()
    if pg_functions is not IGNORED:
        fn_names = _parse_function_names(pg_functions, conn)
        functions = [f for f in canonical.functions if (f.schema, f.name) in fn_names]
        if pg_functions and not functions:
            log.warning("No canonical functions matched user DDL — check schema qualifiers in pg_functions")

    triggers: Sequence[TriggerInfo] = ()
    if pg_triggers is not IGNORED:
        trg_ids = _parse_trigger_identities(pg_triggers, conn)
        triggers = [t for t in canonical.triggers if (t.schema, t.table_name, t.trigger_name) in trg_ids]
        if pg_triggers and not triggers:
            log.warning("No canonical triggers matched user DDL — check schema qualifiers in pg_triggers")

    views: Sequence[ViewInfo] = ()
    if pg_views is not IGNORED:
        view_names = _parse_view_names(pg_views, conn)
        views = [v for v in canonical.views if (v.schema, v.name) in view_names]
        if pg_views and not views:
            log.warning("No canonical views matched user DDL — check schema qualifiers in pg_views")

    fn_dropped = len(canonical.functions) - len(functions)
    trg_dropped = len(canonical.triggers) - len(triggers)
    view_dropped = len(canonical.views) - len(views)
    if fn_dropped or trg_dropped or view_dropped:
        log.debug(
            "Filtered out %d functions, %d triggers, and %d views not in user DDL",
            fn_dropped,
            trg_dropped,
            view_dropped,
        )

    return CanonicalState(functions=functions, triggers=triggers, views=views)


def _parse_function_names(ddl_list: Sequence[str], conn: Connection) -> set[tuple[str, str]]:
    """Extract ``(schema, name)`` pairs from function DDL strings via postgast.

    Raises:
        ValueError: If any DDL string does not contain a valid ``CREATE FUNCTION`` statement.
    """
    import postgast

    default_schema = current_schema(conn)
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

    default_schema = current_schema(conn)
    identities: set[tuple[str, str, str]] = set()
    for ddl in ddl_list:
        identity = postgast.extract_trigger_identity(postgast.parse(ddl))
        if identity is None:
            raise ValueError(f"Cannot parse trigger identity from pg_triggers DDL: {ddl!r}")
        schema = identity.schema if identity.schema is not None else default_schema
        identities.add((schema, identity.table, identity.trigger))
    return identities


def _parse_view_names(ddl_list: Sequence[str], conn: Connection) -> set[tuple[str, str]]:
    """Extract ``(schema, name)`` pairs from view DDL strings via postgast.

    Raises:
        ValueError: If any DDL string does not contain a valid ``CREATE VIEW`` statement.
    """
    import postgast
    from postgast.pg_query_pb2 import ViewStmt

    default_schema = current_schema(conn)
    names: set[tuple[str, str]] = set()
    for ddl in ddl_list:
        view = next((node.view for node in postgast.find_nodes(postgast.parse(ddl), ViewStmt)), None)
        if view is None:
            raise ValueError(f"Cannot parse view identity from pg_views DDL: {ddl!r}")
        # ``schemaname`` is the empty string, not None, when the DDL leaves the view unqualified.
        names.add((view.schemaname or default_schema, view.relname))
    return names


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
            resolved.append(current_schema(conn))
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
        views=[v for v in state.views if v.schema in schema_set],
        grants=[g for g in state.grants if g.schema in schema_set],
    )


def _grant_states(
    conn: Connection,
    pg_grants: Sequence[str],
    schemas: Sequence[str] | None,
) -> tuple[Sequence[GrantInfo], Sequence[GrantInfo]]:
    """Return the current and the desired grants, without the initial privileges of the database.

    ``initdb`` gives ``USAGE`` on the ``public`` schema to ``PUBLIC``, and ``CREATE EXTENSION`` gives privileges on
    extension objects. ``pg_init_privs`` records these initial privileges. They are not part of the desired state, so
    this function removes them from both sides by identity. Without this rule, a ``pg_grants`` list that does not
    mention ``public`` revokes ``USAGE`` on it from every role. A user who revoked an initial privilege by hand also
    does not get it back.
    """
    initial = {grant[:-1] for grant in inspect_grants(conn, schemas, initial=True)}
    current = [grant for grant in inspect_grants(conn, schemas) if grant[:-1] not in initial]
    schema_set = None if schemas is None else set(schemas)
    desired = [
        grant
        for grant in canonicalize_grants(conn, pg_grants)
        if grant[:-1] not in initial and (schema_set is None or grant.schema in schema_set)
    ]
    if pg_grants and not desired:
        log.warning("No declared grants are in the compared schemas. Check the schema qualifiers in pg_grants.")
    return current, desired


def _grant_migration_ops(grant_ops: Sequence[GrantOp]) -> tuple[list[MigrateOperation], list[MigrateOperation]]:
    """Group per-privilege diff ops into one migration op per object, grantee, and kind of change.

    Returns ``(early, late)``. *early* holds the revokes and the grant option changes. They apply to objects that exist
    before the migration. *late* holds the new grants. Their objects can be new in the same migration.
    """
    early: list[MigrateOperation] = []
    late: list[MigrateOperation] = []
    revokes = [op.current for op in grant_ops if op.action is Action.DROP and op.current is not None]
    options = [op.desired for op in grant_ops if op.action is Action.REPLACE and op.desired is not None]
    grants = [op.desired for op in grant_ops if op.action is Action.CREATE and op.desired is not None]
    early.extend(RevokePrivilegesOp(group) for group in _group_grants(revokes))
    early.extend(AlterGrantOptionOp(group) for group in _group_grants(options))
    late.extend(GrantPrivilegesOp(group) for group in _group_grants(grants))
    return early, late


def _group_grants(grants: Sequence[GrantInfo]) -> list[list[GrantInfo]]:
    """Split *grants* into groups that share object, grantee, and ``grantable``, so one statement holds each group."""

    def group_key(grant: GrantInfo) -> tuple[str, str, str, str, bool]:
        return (grant.schema, grant.object_type, grant.name, grant.grantee, grant.grantable)

    return [list(group) for _, group in itertools.groupby(sorted(grants, key=group_key), key=group_key)]


def _order_ops(
    function_ops: Sequence[FunctionOp],
    trigger_ops: Sequence[TriggerOp],
    view_ops: Sequence[ViewOp],
) -> list[MigrateOperation]:
    """Convert diff ops to MigrateOperation instances in dependency-safe order.

    Order: drop triggers, drop views, drop functions, create/replace functions, create/replace views,
    create/replace triggers.
    """
    result: list[MigrateOperation] = []

    # 1. Drop triggers first (frees views and functions for removal)
    for op in trigger_ops:
        if op.action is Action.DROP:
            assert op.current is not None
            result.append(DropTriggerOp(op.current))

    # 2. Drop views (frees functions for removal)
    for op in view_ops:
        if op.action is Action.DROP:
            assert op.current is not None
            result.append(DropViewOp(op.current))

    # 3. Drop functions
    for op in function_ops:
        if op.action is Action.DROP:
            assert op.current is not None
            result.append(DropFunctionOp(op.current))

    # 4. Create/replace functions (must exist before views reference them)
    for op in function_ops:
        if op.action is Action.CREATE:
            assert op.desired is not None
            result.append(CreateFunctionOp(op.desired))
        elif op.action is Action.REPLACE:
            assert op.current is not None and op.desired is not None
            result.append(ReplaceFunctionOp(op.current, op.desired))

    # 5. Create/replace views (must exist before INSTEAD OF triggers reference them)
    for op in view_ops:
        if op.action is Action.CREATE:
            assert op.desired is not None
            result.append(CreateViewOp(op.desired))
        elif op.action is Action.REPLACE:
            assert op.current is not None and op.desired is not None
            result.append(ReplaceViewOp(op.current, op.desired))

    # 6. Create/replace triggers
    for op in trigger_ops:
        if op.action is Action.CREATE:
            assert op.desired is not None
            result.append(CreateTriggerOp(op.desired))
        elif op.action is Action.REPLACE:
            assert op.current is not None and op.desired is not None
            result.append(ReplaceTriggerOp(op.current, op.desired))

    return result
