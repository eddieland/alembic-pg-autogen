"""Desired-state canonicalization through PostgreSQL round-tripping."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.schema import CreateIndex

from alembic_pg_autogen.inspect import (
    GrantInfo,
    IndexInfo,
    current_schema,
    inspect_functions,
    inspect_triggers,
    inspect_views,
)
from alembic_pg_autogen.sentinels import IGNORED

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from collections.abc import Set as AbstractSet
    from typing import Final

    from postgast.pg_query_pb2 import GrantStmt, RoleSpec
    from sqlalchemy import Connection, Index

    from alembic_pg_autogen.inspect import FunctionInfo, TriggerInfo, ViewInfo
    from alembic_pg_autogen.sentinels import Ignored


class CanonicalState(NamedTuple):
    """Post-DDL catalog snapshot returned by :func:`canonicalize`."""

    functions: Sequence[FunctionInfo]
    triggers: Sequence[TriggerInfo]
    views: Sequence[ViewInfo] = ()


def canonicalize(
    conn: Connection,
    *,
    function_ddl: Sequence[str] | Ignored = (),
    view_ddl: Sequence[str] | Ignored = (),
    trigger_ddl: Sequence[str] | Ignored = (),
    schemas: Sequence[str] | None = None,
) -> CanonicalState:
    """Canonicalize user-provided DDL by round-tripping through PostgreSQL.

    Executes the given DDL statements inside a savepoint, reads back canonical forms via ``inspect_functions`` /
    ``inspect_views`` / ``inspect_triggers``, then rolls back the savepoint — leaving the database unchanged.

    DDL executes in dependency order: functions first (standalone), then views (may reference functions), then triggers
    (may reference functions and INSTEAD OF triggers may be on views).

    An object type passed as :data:`~alembic_pg_autogen.IGNORED` is skipped entirely: no DDL is executed for it and its
    catalog is not read back, so the corresponding :class:`CanonicalState` field is empty.

    Args:
        conn: An open SQLAlchemy connection (may have an active transaction).
        function_ddl: ``CREATE FUNCTION`` / ``CREATE PROCEDURE`` statements, or :data:`~alembic_pg_autogen.IGNORED`.
        view_ddl: ``CREATE VIEW`` statements, or :data:`~alembic_pg_autogen.IGNORED`.
        trigger_ddl: ``CREATE TRIGGER`` statements, or :data:`~alembic_pg_autogen.IGNORED`.
        schemas: Optional schema list passed to the inspect helpers.  When *None*, all user schemas are included.

    Returns:
        A :class:`CanonicalState` with the full post-DDL catalog state.

    Raises:
        sqlalchemy.exc.DBAPIError: If any DDL statement is invalid.
    """
    function_stmts = _declared(function_ddl)
    view_stmts = _declared(view_ddl)
    trigger_stmts = _declared(trigger_ddl)
    log.info(
        "Canonicalizing %d function, %d view, and %d trigger DDL statements",
        len(function_stmts),
        len(view_stmts),
        len(trigger_stmts),
    )
    import postgast

    functions: Sequence[FunctionInfo] = ()
    views: Sequence[ViewInfo] = ()
    triggers: Sequence[TriggerInfo] = ()

    savepoint = conn.begin_nested()
    try:
        for ddl in function_stmts:
            conn.execute(text(postgast.ensure_or_replace(ddl)))
        for ddl in view_stmts:
            conn.execute(text(postgast.ensure_or_replace(ddl)))
        for ddl in trigger_stmts:
            conn.execute(text(postgast.ensure_or_replace(ddl)))

        if function_ddl is not IGNORED:
            functions = inspect_functions(conn, schemas)
        if view_ddl is not IGNORED:
            views = inspect_views(conn, schemas)
        if trigger_ddl is not IGNORED:
            triggers = inspect_triggers(conn, schemas)
    finally:
        savepoint.rollback()
        log.debug("Canonicalization savepoint rolled back")

    if function_stmts and not functions:
        log.warning("Canonicalization produced no functions despite %d function DDL statements", len(function_stmts))
    if view_stmts and not views:
        log.warning("Canonicalization produced no views despite %d view DDL statements", len(view_stmts))
    if trigger_stmts and not triggers:
        log.warning("Canonicalization produced no triggers despite %d trigger DDL statements", len(trigger_stmts))

    return CanonicalState(functions=functions, triggers=triggers, views=views)


def canonicalize_functions(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[FunctionInfo]:
    """Canonicalize function DDL and return the resulting ``FunctionInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *function_ddl* populated; the other object types are
    ignored, so their catalogs are not read back.
    """
    return canonicalize(
        conn,
        function_ddl=ddl,
        view_ddl=IGNORED,
        trigger_ddl=IGNORED,
        schemas=schemas,
    ).functions


def canonicalize_triggers(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[TriggerInfo]:
    """Canonicalize trigger DDL and return the resulting ``TriggerInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *trigger_ddl* populated; the other object types are
    ignored, so their catalogs are not read back.
    """
    return canonicalize(
        conn,
        trigger_ddl=ddl,
        function_ddl=IGNORED,
        view_ddl=IGNORED,
        schemas=schemas,
    ).triggers


def canonicalize_views(
    conn: Connection,
    ddl: Sequence[str],
    schemas: Sequence[str] | None = None,
) -> Sequence[ViewInfo]:
    """Canonicalize view DDL and return the resulting ``ViewInfo`` list.

    Convenience wrapper around :func:`canonicalize` with only *view_ddl* populated; the other object types are
    ignored, so their catalogs are not read back.
    """
    return canonicalize(
        conn,
        view_ddl=ddl,
        function_ddl=IGNORED,
        trigger_ddl=IGNORED,
        schemas=schemas,
    ).views


def canonicalize_check_constraints(
    conn: Connection,
    *,
    schema: str | None,
    table_name: str,
    expressions: Mapping[str, str],
) -> Mapping[str, str]:
    """Canonicalize desired ``CHECK`` expressions by round-tripping them through PostgreSQL.

    Each expression is added to the live table as a throwaway ``NOT VALID`` constraint inside a savepoint, read back
    with ``pg_get_expr()``, and then rolled back — leaving the database unchanged.  ``NOT VALID`` keeps the round-trip
    cheap: PostgreSQL skips the full-table validation scan, so no existing row is examined and rows that would violate
    a newly declared constraint do not turn autogenerate into an error.

    The returned expressions are directly comparable with :attr:`CheckConstraintInfo.expression
    <alembic_pg_autogen.inspect.CheckConstraintInfo.expression>`, which is read back through the same
    ``pg_get_expr()`` deparse.  That is the whole point of the round-trip: ``amount >= 0`` in a SQLAlchemy model and
    ``(amount >= (0)::numeric)`` in the catalog are the same constraint, and only PostgreSQL can say so.

    Args:
        conn: An open SQLAlchemy connection (may have an active transaction).
        schema: Schema qualifying *table_name*, or *None* to resolve it through the connection's ``search_path``.
        table_name: The table the constraints belong to.  It must already exist in the database.
        expressions: Mapping of constraint name to the raw ``CHECK`` expression text to normalize.

    Returns:
        A mapping of constraint name to normalized expression.  Names whose expression could not be applied — an
        invalid expression, a column that does not exist yet, a table the connection may not lock — are absent from
        the result rather than raising, so a single unusable constraint cannot break autogenerate.
    """
    if not expressions:
        return {}

    preparer = conn.dialect.identifier_preparer
    qualified = preparer.quote(table_name)
    if schema is not None:
        qualified = f"{preparer.quote_schema(schema)}.{qualified}"
    probes = {f"{_PROBE_PREFIX}{index}": name for index, name in enumerate(expressions)}

    savepoint = conn.begin_nested()
    try:
        for probe, name in probes.items():
            # The expression is user-authored SQL from the model's own metadata, interpolated the same way the rest of
            # this module interpolates user DDL.  It only ever runs inside this savepoint.
            conn.execute(
                text(
                    f"ALTER TABLE {qualified} ADD CONSTRAINT {preparer.quote(probe)} "
                    f"CHECK ({expressions[name]}) NOT VALID"
                )
            )
        rows = conn.execute(text(_PROBE_QUERY), {"table": qualified, "names": list(probes)}).all()
        normalized = {probes[row.name]: row.expression for row in rows}
    except DBAPIError:
        log.warning(
            "Could not canonicalize check constraints on %s; treating them as unchanged", qualified, exc_info=True
        )
        normalized = {}
    finally:
        savepoint.rollback()
        log.debug("Check constraint canonicalization savepoint rolled back")

    missing = set(expressions) - set(normalized)
    if missing:
        log.warning("Canonicalization produced no expression for check constraints: %s", sorted(missing))

    return normalized


def canonicalize_indexes(
    conn: Connection,
    *,
    schema: str | None,
    table_name: str,
    indexes: Mapping[str, Index],
) -> Mapping[str, IndexInfo]:
    """Canonicalize desired indexes through a round trip in PostgreSQL.

    The function creates each index on an empty ``TEMP`` copy of the target table, inside a savepoint. It reads the
    index back with ``pg_get_indexdef()``. Then it rolls back the savepoint, so the database does not change. The
    result compares directly with :func:`inspect_indexes <alembic_pg_autogen.inspect.inspect_indexes>`, which reads
    the live catalog through the same deparse. For example, ``WHERE status IN ('a', 'b')`` in a SQLAlchemy model and
    ``WHERE (status = ANY (ARRAY['a'::text, 'b'::text]))`` in the catalog are the same index. Only PostgreSQL can
    confirm this.

    The copy keeps the test fast. A ``NOT VALID`` check constraint skips validation, but ``CREATE INDEX`` builds the
    index. On a table with 500,000 rows, an expression index took about 1.2 seconds and a GIN index took about 2.1
    seconds. On an empty copy, each took less than one millisecond. The copy also prevents a lock on the real table for
    the rest of the transaction. ``CREATE TEMP TABLE ... (LIKE ...)`` copies the column names, types, and collations
    that the deparse uses. Thus the copy gives the same canonical form as the real table.

    The copy has the name of the target table inside ``pg_temp``. The index DDL runs under a ``schema_translate_map``
    that changes the schema of the table to ``pg_temp``. The function needs both mechanisms. The map handles metadata
    with an explicit schema. The shared name handles metadata with no explicit schema.

    Args:
        conn: An open SQLAlchemy connection. It can have an active transaction.
        schema: The schema of *table_name*. With *None*, the ``search_path`` of the connection finds the table.
        table_name: The table of the indexes. The table must exist in the database.
        indexes: A mapping of index name to the SQLAlchemy :class:`~sqlalchemy.schema.Index` to normalize.

    Returns:
        A mapping of index name to :class:`~alembic_pg_autogen.inspect.IndexInfo`. Each entry holds *schema* and
        *table_name* as given, so the result compares directly with the live catalog. If the function cannot create an
        index, the result does not contain its name. The function does not raise, so one bad index cannot stop
        autogenerate.
    """
    if not indexes:
        return {}

    preparer = conn.dialect.identifier_preparer
    qualified = preparer.quote(table_name)
    if schema is not None:
        qualified = f"{preparer.quote_schema(schema)}.{qualified}"

    normalized: dict[str, IndexInfo] = {}
    savepoint = conn.begin_nested()
    try:
        conn.execute(text(f"CREATE TEMP TABLE {preparer.quote(table_name)} (LIKE {qualified})"))
    except SQLAlchemyError:
        # The copy must have the name of the target table, or the DDL does not find it. A temporary relation with
        # that name on this connection therefore blocks the test. The table itself has no problem. The function
        # has no place to create the test indexes, so it does not compare them in this run.
        log.warning(
            "Could not create a temporary probe clone of %s; treating its indexes as unchanged. A temporary relation "
            "already named %r on this connection would cause this.",
            qualified,
            table_name,
        )
        log.debug("Probe clone creation failed for %s", qualified, exc_info=True)
        savepoint.rollback()
        return {}

    try:
        # ``None`` covers metadata with no explicit schema. *schema* covers metadata with an explicit schema.
        probe_conn = conn.execution_options(schema_translate_map={None: _TEMP_SCHEMA, schema: _TEMP_SCHEMA})
        created: list[str] = []
        for name, index in indexes.items():
            probe = conn.begin_nested()
            # Catch ``SQLAlchemyError``, not ``DBAPIError``. SQLAlchemy compiles the statement inside ``execute()``.
            # An index that SQLAlchemy cannot render raises ``CompileError`` before the server gets the statement.
            # An example is an expression whose type has no literal renderer. When the code caught only the server
            # error, one such index stopped the run and the comparison of all other indexes on the table.
            try:
                probe_conn.execute(CreateIndex(index))
            except SQLAlchemyError:
                probe.rollback()
                log.warning("Could not canonicalize index %r on %s; treating it as unchanged", name, qualified)
                log.debug("Index canonicalization failure for %r", name, exc_info=True)
                continue
            probe.commit()
            created.append(name)

        if created:
            rows = conn.execute(text(_INDEX_PROBE_QUERY), {"names": created}).all()
            for row in rows:
                if row.shape is None:
                    log.warning(
                        "Could not normalize the probed definition of index %r; treating it as unchanged", row.name
                    )
                    continue
                normalized[row.name] = IndexInfo(
                    schema=schema if schema is not None else current_schema(conn),
                    table_name=table_name,
                    name=row.name,
                    unique=row.unique,
                    shape=row.shape,
                )
    except SQLAlchemyError:
        log.warning("Could not canonicalize indexes on %s; treating them as unchanged", qualified, exc_info=True)
        normalized = {}
    finally:
        savepoint.rollback()
        log.debug("Index canonicalization savepoint rolled back")

    missing = set(indexes) - set(normalized)
    if missing:
        log.warning("Canonicalization produced no definition for indexes: %s", sorted(missing))

    return normalized


def canonicalize_grants(conn: Connection, ddl: Sequence[str]) -> Sequence[GrantInfo]:
    """Parse ``GRANT`` statements into the :class:`~alembic_pg_autogen.inspect.GrantInfo` form of the catalog.

    The function does not execute the statements. A grant can name a table that the same migration creates, so the
    table often does not exist yet. The parser of PostgreSQL (through postgast) reads each statement. The function then
    expands it to one ``GrantInfo`` per object, grantee, and privilege, as :func:`inspect_grants
    <alembic_pg_autogen.inspect.inspect_grants>` does. The connection supplies these values:

    - ``to_regclass()`` finds an existing relation through the ``search_path``, as the ``GRANT`` would. A name that
      finds no relation gets ``current_schema()``, because the migration creates that table in the first schema of
      the ``search_path``.
    - ``current_user`` and ``session_user`` replace ``CURRENT_USER``, ``CURRENT_ROLE``, and ``SESSION_USER``.
    - ``acldefault()`` gives the privileges of ``ALL`` for each object type. The set depends on the server version.
      For example, PostgreSQL 17 adds ``MAINTAIN`` for tables.

    Each statement must be a ``GRANT`` on named tables or schemas. A string can hold more than one statement.
    Equal grants merge into one. ``grantable`` is ``True`` if one of them has ``WITH GRANT OPTION``.

    Args:
        conn: An open SQLAlchemy connection.
        ddl: ``GRANT ... ON [TABLE] ...`` and ``GRANT ... ON SCHEMA ...`` statements.

    Returns:
        A sequence of :class:`~alembic_pg_autogen.inspect.GrantInfo` instances, sorted by identity.

    Raises:
        ValueError: If a statement is not a ``GRANT``, or if it uses a form that this library does not compare:
            ``REVOKE``, ``ON ALL TABLES IN SCHEMA``, ``GRANTED BY``, ``WITH GRANT OPTION`` for ``PUBLIC``, another
            object type, column privileges, or a privilege that the object type does not have. A name in
            ``ON [TABLE]`` that finds a sequence or another relation that is not a table also raises.
    """
    return parse_grants(conn, ddl).grants


class DeclaredGrants(NamedTuple):
    """The result of :func:`parse_grants`: the grants, and the targets that a statement declared with ``ALL``."""

    grants: Sequence[GrantInfo]
    """The grants in the form of :func:`canonicalize_grants`."""

    all_privileges: AbstractSet[tuple[str, str, str, str]]
    """The ``(schema, object_type, name, grantee)`` targets of ``GRANT ALL [PRIVILEGES]`` statements.

    The comparator renders ``ALL`` only for these targets. ``ALL`` means the privilege set of the server that runs the
    migration, so it must not replace a list of privileges that the declaration names one by one.
    """


def parse_grants(conn: Connection, ddl: Sequence[str]) -> DeclaredGrants:
    """Parse ``GRANT`` statements as :func:`canonicalize_grants` does, and also report which targets used ``ALL``.

    Raises:
        ValueError: For the statements that :func:`canonicalize_grants` rejects.
    """
    if not ddl:
        return DeclaredGrants((), frozenset())

    import postgast

    context = _GrantContext.read(conn)
    grantable_by_key: dict[tuple[str, str, str, str, str], bool] = {}
    all_targets: set[tuple[str, str, str, str]] = set()
    for statement in ddl:
        for raw in postgast.parse(statement).stmts:
            if raw.stmt.WhichOneof("node") != "grant_stmt":
                raise ValueError(f"pg_grants accepts only GRANT statements: {statement!r}")
            grant_stmt = raw.stmt.grant_stmt
            for grant in _expand_grant(conn, grant_stmt, context, statement):
                key = grant[:-1]
                grantable_by_key[key] = grantable_by_key.get(key, False) or grant.grantable
                if not grant_stmt.privileges:  # ``GRANT ALL [PRIVILEGES]`` has an empty privilege list
                    all_targets.add(grant[:3] + (grant.grantee,))

    grants = [GrantInfo(*key, grantable=grantable) for key, grantable in sorted(grantable_by_key.items())]
    log.debug("Canonicalized %d GRANT statements into %d grants", len(ddl), len(grants))
    return DeclaredGrants(grants, frozenset(all_targets))


def all_grant_privileges(conn: Connection) -> Mapping[str, AbstractSet[str]]:
    """Return the privileges of ``GRANT ALL`` on the connected server, for ``"TABLE"`` and for ``"SCHEMA"``.

    The comparator uses the result to render ``ALL`` for a statement that holds every privilege of its object type.
    A migration with ``ALL`` then runs on a server version with fewer privileges, too.
    """
    row = conn.execute(text(_GRANT_CONTEXT_QUERY)).one()
    return {"TABLE": frozenset(row.table_privileges), "SCHEMA": frozenset(row.schema_privileges)}


class _GrantContext(NamedTuple):
    """Values from the server that :func:`canonicalize_grants` needs to expand a ``GRANT`` statement."""

    schema: str
    current_user: str
    session_user: str
    all_privileges: Mapping[str, Sequence[str]]

    @classmethod
    def read(cls, conn: Connection) -> _GrantContext:
        """Read the values in one query."""
        row = conn.execute(text(_GRANT_CONTEXT_QUERY)).one()
        return cls(
            schema=row.schema,
            current_user=row.current_user,
            session_user=row.session_user,
            all_privileges={"TABLE": tuple(row.table_privileges), "SCHEMA": tuple(row.schema_privileges)},
        )


_GRANT_CONTEXT_QUERY = """\
SELECT
    current_schema() AS schema,
    current_user AS current_user,
    session_user AS session_user,
    ARRAY(SELECT privilege_type FROM pg_catalog.aclexplode(pg_catalog.acldefault('r', 10::oid))) AS table_privileges,
    ARRAY(SELECT privilege_type FROM pg_catalog.aclexplode(pg_catalog.acldefault('n', 10::oid))) AS schema_privileges
"""
"""Read the default schema, the session roles, and the privileges of ``ALL`` for tables and schemas.

``acldefault()`` returns the default ACL of an owner. The owner holds every privilege of the object type, so the
privileges in that ACL are the privileges of ``ALL``. Role 10 is the bootstrap superuser. The role does not matter,
because the query reads only the privilege names.
"""

_RELATION_QUERY = """\
SELECT n.nspname AS schema, c.relkind AS kind
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE c.oid = pg_catalog.to_regclass(
    CASE
        WHEN CAST(:schema AS text) IS NULL THEN pg_catalog.quote_ident(:name)
        ELSE pg_catalog.quote_ident(:schema) || '.' || pg_catalog.quote_ident(:name)
    END
)
"""
"""Find a relation as ``GRANT`` finds it: through the ``search_path`` when the name has no schema.

``quote_ident()`` runs in SQL, so the names reach the server as bind parameters and need no quoting in Python.
"""

_TABLE_KINDS: Final = frozenset({"r", "p", "v", "m", "f"})
"""``relkind`` values that ``GRANT ... ON TABLE`` covers in this library: tables, views, and foreign tables."""

_RELATION_KIND_NAMES: Final[Mapping[str, str]] = {
    "S": "a sequence",
    "i": "an index",
    "I": "a partitioned index",
    "c": "a composite type",
    "t": "a TOAST table",
}


def _expand_grant(conn: Connection, grant: GrantStmt, context: _GrantContext, statement: str) -> list[GrantInfo]:
    """Expand one parsed ``GRANT`` to one :class:`GrantInfo` per object, grantee, and privilege."""
    from postgast.pg_query_pb2 import ACL_TARGET_OBJECT, OBJECT_SCHEMA, OBJECT_TABLE, ROLESPEC_PUBLIC

    if not grant.is_grant:
        raise ValueError(f"pg_grants accepts only GRANT statements, not REVOKE: {statement!r}")
    if grant.targtype != ACL_TARGET_OBJECT:
        raise ValueError(f"pg_grants does not support GRANT ... ON ALL ... IN SCHEMA: {statement!r}")
    if grant.HasField("grantor"):
        raise ValueError(
            f"pg_grants does not support GRANTED BY, because the comparison ignores grantors: {statement!r}"
        )
    if grant.grant_option and any(node.role_spec.roletype == ROLESPEC_PUBLIC for node in grant.grantees):
        raise ValueError(f"PostgreSQL does not allow WITH GRANT OPTION for PUBLIC: {statement!r}")

    objects: list[tuple[str, str, str]]
    if grant.objtype == OBJECT_TABLE:
        object_type = "TABLE"
        objects = []
        for node in grant.objects:
            schema = _table_schema(conn, node.range_var.schemaname or None, node.range_var.relname, statement)
            objects.append((schema or context.schema, object_type, node.range_var.relname))
    elif grant.objtype == OBJECT_SCHEMA:
        object_type = "SCHEMA"
        objects = [(node.string.sval, object_type, node.string.sval) for node in grant.objects]
    else:
        raise ValueError(f"pg_grants supports only GRANT ... ON TABLE and GRANT ... ON SCHEMA: {statement!r}")

    allowed = context.all_privileges[object_type]
    privileges: list[str] = []
    for node in grant.privileges:
        access = node.access_priv
        if access.cols:
            raise ValueError(f"pg_grants does not support column privileges: {statement!r}")
        privilege = access.priv_name.upper()
        if privilege not in allowed:
            raise ValueError(f"{privilege} is not a {object_type} privilege in pg_grants: {statement!r}")
        privileges.append(privilege)
    if not privileges:  # ``GRANT ALL [PRIVILEGES]`` has an empty privilege list
        privileges = list(allowed)

    grantees = [_grantee_name(node.role_spec, context) for node in grant.grantees]
    return [
        GrantInfo(schema, kind, name, grantee, privilege, grant.grant_option)
        for schema, kind, name in objects
        for grantee in grantees
        for privilege in privileges
    ]


def _table_schema(conn: Connection, schema: str | None, name: str, statement: str) -> str | None:
    """Return the schema of the relation that ``GRANT ... ON [TABLE]`` names, or *schema* if no relation exists.

    Raises:
        ValueError: If the relation exists and is not a table, a view, or a foreign table. PostgreSQL accepts
            ``GRANT SELECT ON seq`` for a sequence, but :func:`inspect_grants` does not read sequences, so the grant
            would appear again on each run.
    """
    row = conn.execute(text(_RELATION_QUERY), {"schema": schema, "name": name}).one_or_none()
    if row is None:
        return schema
    if row.kind not in _TABLE_KINDS:
        kind = _RELATION_KIND_NAMES.get(row.kind, f"a relation of kind {row.kind!r}")
        raise ValueError(
            f"pg_grants supports only tables, views, and foreign tables. {name!r} is {kind}: {statement!r}"
        )
    return row.schema


def _grantee_name(role: RoleSpec, context: _GrantContext) -> str:
    """Return the role name of a grantee in the form that :func:`inspect_grants` returns."""
    from postgast.pg_query_pb2 import (
        ROLESPEC_CURRENT_ROLE,
        ROLESPEC_CURRENT_USER,
        ROLESPEC_PUBLIC,
        ROLESPEC_SESSION_USER,
    )

    if role.roletype == ROLESPEC_PUBLIC:
        return "public"
    if role.roletype in (ROLESPEC_CURRENT_USER, ROLESPEC_CURRENT_ROLE):
        return context.current_user
    if role.roletype == ROLESPEC_SESSION_USER:
        return context.session_user
    return role.rolename


_TEMP_SCHEMA = "pg_temp"
"""The schema name that the index DDL uses to find the copy.

``pg_temp`` always *finds* the temporary schema of the session. But PostgreSQL does not always *write* the schema with
that name. On PostgreSQL 15 and later, ``pg_get_indexdef()`` writes the table reference through
``get_namespace_name_or_temp()``. That function writes ``pg_temp`` in place of the real name ``pg_temp_N``. PostgreSQL
14 writes the real name, for example ``pg_temp_3.t``. The test query therefore accepts both spellings. See
:data:`_INDEX_PROBE_QUERY`."""

_INDEX_PROBE_QUERY = f"""\
SELECT
    ic.relname AS name,
    i.indisunique AS unique,
    CASE
        WHEN left(d.definition, length(d.aliased)) = d.aliased THEN substr(d.definition, length(d.aliased) + 1)
        WHEN left(d.definition, length(d.qualified)) = d.qualified THEN substr(d.definition, length(d.qualified) + 1)
    END AS shape
FROM pg_catalog.pg_index i
JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid
JOIN pg_catalog.pg_class tc ON tc.oid = i.indrelid
JOIN pg_catalog.pg_namespace tn ON tn.oid = tc.relnamespace
CROSS JOIN LATERAL (
    SELECT
        pg_catalog.pg_get_indexdef(i.indexrelid) AS definition,
        h.head || '{_TEMP_SCHEMA}.' || pg_catalog.quote_ident(tc.relname) || ' ' AS aliased,
        h.head || pg_catalog.quote_ident(tn.nspname) || '.' || pg_catalog.quote_ident(tc.relname) || ' ' AS qualified
    FROM (
        SELECT 'CREATE '
            || CASE WHEN i.indisunique THEN 'UNIQUE ' ELSE '' END
            || 'INDEX '
            || pg_catalog.quote_ident(ic.relname)
            || ' ON ' AS head
    ) h
) d
WHERE ic.relname = ANY(:names)
  AND tc.relnamespace = pg_my_temp_schema()
"""
"""Read the test indexes back, and remove the identity before the shape.

The query accepts two spellings of the table reference of the copy. PostgreSQL 15 and later write the temporary schema
as ``pg_temp``. PostgreSQL 14 writes the real name, for example ``pg_temp_3``. The query tries ``pg_temp`` first and
the real name second. Thus it does not need the server version. If neither matches, the query returns NULL. The caller
then logs "could not normalize" and reports the index as unchanged. This result is safe. A partly stripped shape can
never equal a catalog shape, so it causes a permanent false difference.
"""

_PROBE_PREFIX = "_alembic_pg_autogen_probe_"

_PROBE_QUERY = """\
SELECT
    con.conname AS name,
    pg_catalog.pg_get_expr(con.conbin, con.conrelid, true) AS expression
FROM pg_catalog.pg_constraint con
WHERE con.conrelid = CAST(:table AS regclass)
  AND con.conname = ANY(:names)
"""


def _declared(ddl: Sequence[str] | Ignored) -> Sequence[str]:
    """Return the DDL statements to execute, treating :data:`~alembic_pg_autogen.IGNORED` as "none"."""
    return () if ddl is IGNORED else ddl
