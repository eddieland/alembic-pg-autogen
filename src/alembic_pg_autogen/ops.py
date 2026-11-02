"""Custom MigrateOperation subclasses for PostgreSQL objects."""

# ruff: noqa: D107  # __init__ signatures are self-documenting; class docstrings suffice.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from alembic.operations.ops import CreateCheckConstraintOp, MigrateOperation
from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Sequence

    from alembic.operations.ops import CreateIndexOp, DropConstraintOp, DropIndexOp
    from sqlalchemy import Constraint
    from sqlalchemy.sql.elements import ColumnElement, TextClause

    from alembic_pg_autogen.inspect import FunctionInfo, GrantInfo, TriggerInfo, ViewInfo


class CreateFunctionOp(MigrateOperation):
    """Create a new PostgreSQL function."""

    desired: FunctionInfo

    def __init__(self, desired: FunctionInfo) -> None:
        self.desired = desired

    @override
    def reverse(self) -> DropFunctionOp:
        """Reverse is dropping the newly created function."""
        return DropFunctionOp(self.desired)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("create_function", self.desired.schema, self.desired.name, self.desired.identity_args)


class ReplaceFunctionOp(MigrateOperation):
    """Replace an existing PostgreSQL function with a new definition."""

    current: FunctionInfo
    desired: FunctionInfo

    def __init__(self, current: FunctionInfo, desired: FunctionInfo) -> None:
        self.current = current
        self.desired = desired

    @override
    def reverse(self) -> ReplaceFunctionOp:
        """Reverse is replacing with the old definition."""
        return ReplaceFunctionOp(self.desired, self.current)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("replace_function", self.desired.schema, self.desired.name, self.desired.identity_args)


class DropFunctionOp(MigrateOperation):
    """Drop an existing PostgreSQL function."""

    current: FunctionInfo

    def __init__(self, current: FunctionInfo) -> None:
        self.current = current

    @override
    def reverse(self) -> CreateFunctionOp:
        """Reverse is recreating the dropped function."""
        return CreateFunctionOp(self.current)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("drop_function", self.current.schema, self.current.name, self.current.identity_args)


class CreateTriggerOp(MigrateOperation):
    """Create a new PostgreSQL trigger."""

    desired: TriggerInfo

    def __init__(self, desired: TriggerInfo) -> None:
        self.desired = desired

    @override
    def reverse(self) -> DropTriggerOp:
        """Reverse is dropping the newly created trigger."""
        return DropTriggerOp(self.desired)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("create_trigger", self.desired.schema, self.desired.table_name, self.desired.trigger_name)


class ReplaceTriggerOp(MigrateOperation):
    """Replace an existing PostgreSQL trigger with a new definition."""

    current: TriggerInfo
    desired: TriggerInfo

    def __init__(self, current: TriggerInfo, desired: TriggerInfo) -> None:
        self.current = current
        self.desired = desired

    @override
    def reverse(self) -> ReplaceTriggerOp:
        """Reverse is replacing with the old definition."""
        return ReplaceTriggerOp(self.desired, self.current)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("replace_trigger", self.desired.schema, self.desired.table_name, self.desired.trigger_name)


class DropTriggerOp(MigrateOperation):
    """Drop an existing PostgreSQL trigger."""

    current: TriggerInfo

    def __init__(self, current: TriggerInfo) -> None:
        self.current = current

    @override
    def reverse(self) -> CreateTriggerOp:
        """Reverse is recreating the dropped trigger."""
        return CreateTriggerOp(self.current)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("drop_trigger", self.current.schema, self.current.table_name, self.current.trigger_name)


class CreateViewOp(MigrateOperation):
    """Create a new PostgreSQL view."""

    desired: ViewInfo

    def __init__(self, desired: ViewInfo) -> None:
        self.desired = desired

    @override
    def reverse(self) -> DropViewOp:
        """Reverse is dropping the newly created view."""
        return DropViewOp(self.desired)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("create_view", self.desired.schema, self.desired.name)


class ReplaceViewOp(MigrateOperation):
    """Replace an existing PostgreSQL view with a new definition."""

    current: ViewInfo
    desired: ViewInfo

    def __init__(self, current: ViewInfo, desired: ViewInfo) -> None:
        self.current = current
        self.desired = desired

    @override
    def reverse(self) -> ReplaceViewOp:
        """Reverse is replacing with the old definition."""
        return ReplaceViewOp(self.desired, self.current)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("replace_view", self.desired.schema, self.desired.name)


class DropViewOp(MigrateOperation):
    """Drop an existing PostgreSQL view."""

    current: ViewInfo

    def __init__(self, current: ViewInfo) -> None:
        self.current = current

    @override
    def reverse(self) -> CreateViewOp:
        """Reverse is recreating the dropped view."""
        return CreateViewOp(self.current)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("drop_view", self.current.schema, self.current.name)


class GrantPrivilegesOp(MigrateOperation):
    """Grant privileges on one table or schema to one role with ``GRANT``.

    Each item of ``grants`` holds one privilege. All items must have the same object, grantee, and ``grantable`` flag,
    so the operation renders as one statement. ``grantable`` adds ``WITH GRANT OPTION``.
    """

    grants: tuple[GrantInfo, ...]

    def __init__(self, grants: Sequence[GrantInfo]) -> None:
        self.grants = _check_grant_group(grants)

    @override
    def reverse(self) -> RevokePrivilegesOp:
        """Reverse is revoking the granted privileges."""
        return RevokePrivilegesOp(self.grants)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str, str, str, tuple[str, ...]]:
        """Return a hashable tuple for debugging and comparison."""
        return ("grant_privileges", *_grant_group_key(self.grants))


class RevokePrivilegesOp(MigrateOperation):
    """Revoke privileges on one table or schema from one role with ``REVOKE``.

    ``grants`` has the same rules as in :class:`GrantPrivilegesOp`. It holds the privileges as the catalog stores
    them, so :meth:`reverse` restores ``WITH GRANT OPTION`` when the role had it.
    """

    grants: tuple[GrantInfo, ...]

    def __init__(self, grants: Sequence[GrantInfo]) -> None:
        self.grants = _check_grant_group(grants)

    @override
    def reverse(self) -> GrantPrivilegesOp:
        """Reverse is granting the revoked privileges again."""
        return GrantPrivilegesOp(self.grants)

    @override
    def to_diff_tuple(self) -> tuple[str, str, str, str, str, tuple[str, ...]]:
        """Return a hashable tuple for debugging and comparison."""
        return ("revoke_privileges", *_grant_group_key(self.grants))


class AlterGrantOptionOp(MigrateOperation):
    """Add or remove the grant option of privileges that a role already holds.

    ``grants`` holds the privileges with the *new* ``grantable`` flag. When the flag is ``True``, the operation renders
    ``GRANT ... WITH GRANT OPTION``. When it is ``False``, the operation renders ``REVOKE GRANT OPTION FOR ...``. Both
    statements keep the privilege itself.
    """

    grants: tuple[GrantInfo, ...]

    def __init__(self, grants: Sequence[GrantInfo]) -> None:
        self.grants = _check_grant_group(grants)

    @override
    def reverse(self) -> AlterGrantOptionOp:
        """Reverse is the opposite change of the grant option."""
        return AlterGrantOptionOp([grant._replace(grantable=not grant.grantable) for grant in self.grants])

    @override
    def to_diff_tuple(self) -> tuple[str, str, str, str, str, tuple[str, ...], bool]:
        """Return a hashable tuple for debugging and comparison."""
        return ("alter_grant_option", *_grant_group_key(self.grants), self.grants[0].grantable)


class ValidateConstraintOp(MigrateOperation):
    """Validate a ``NOT VALID`` check constraint with ``ALTER TABLE ... VALIDATE CONSTRAINT``.

    PostgreSQL scans the table under ``SHARE UPDATE EXCLUSIVE``, which blocks neither reads nor writes.  The comparator
    emits this operation when the catalog holds a constraint as ``NOT VALID`` and the metadata constraint does not
    declare ``postgresql_not_valid=True``.
    """

    constraint_name: str
    table_name: str
    schema: str | None

    def __init__(self, constraint_name: str, table_name: str, schema: str | None = None) -> None:
        self.constraint_name = constraint_name
        self.table_name = table_name
        self.schema = schema

    @override
    def reverse(self) -> NoOp:
        """Reverse is nothing.  PostgreSQL cannot mark a validated constraint as ``NOT VALID`` again."""
        table = self.table_name if self.schema is None else f"{self.schema}.{self.table_name}"
        return NoOp(
            f"Constraint {self.constraint_name} on {table} stays validated. "
            "PostgreSQL cannot mark a validated constraint as NOT VALID."
        )

    @override
    def to_diff_tuple(self) -> tuple[str, str | None, str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("validate_constraint", self.schema, self.table_name, self.constraint_name)


class CreateCheckConstraintNotValidOp(CreateCheckConstraintOp):
    """Create a check constraint as ``NOT VALID``, so PostgreSQL skips the scan of existing rows.

    Alembic's own renderer drops ``postgresql_not_valid`` from ``op.kw``.  This subclass forces the flag into ``kw``, so
    ``to_constraint()`` and ``reverse()`` work unchanged, and registers a renderer that appends the keyword to the
    rendered ``op.create_check_constraint(...)`` call.

    ``column`` and ``removed_values`` describe a narrowed value set.  When ``removed_values`` is not empty, the renderer
    writes a comment above the call that asks for a backfill before the validation revision.
    """

    column: str | None
    removed_values: tuple[str, ...]

    def __init__(
        self,
        constraint_name: Any,
        table_name: str,
        condition: str | TextClause | ColumnElement[Any],
        *,
        schema: str | None = None,
        column: str | None = None,
        removed_values: Sequence[str] = (),
        **kw: Any,
    ) -> None:
        kw["postgresql_not_valid"] = True
        super().__init__(constraint_name, table_name, condition, schema=schema, **kw)
        self.column = column
        self.removed_values = tuple(removed_values)

    @classmethod
    @override
    def from_constraint(
        cls, constraint: Constraint, *, column: str | None = None, removed_values: Sequence[str] = ()
    ) -> CreateCheckConstraintNotValidOp:
        """Build the operation from a metadata ``CheckConstraint``, recording the values the change removes."""
        op = super().from_constraint(constraint)
        assert isinstance(op, CreateCheckConstraintNotValidOp)
        op.column = column
        op.removed_values = tuple(removed_values)
        return op

    @override
    def reverse(self) -> DropConstraintOp:
        """Reverse is dropping the constraint, exactly as for a validated one."""
        return super().reverse()


class NoOp(MigrateOperation):
    """An operation that executes nothing.

    It renders as ``pass`` followed by a comment that holds ``reason``, so a migration body that contains nothing else
    stays valid Python and tells the reader why.  :meth:`ValidateConstraintOp.reverse` returns one.
    """

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason

    @override
    def reverse(self) -> NoOp:
        """Reverse of nothing is nothing."""
        return NoOp(self.reason)

    @override
    def to_diff_tuple(self) -> tuple[str, str]:
        """Return a hashable tuple for debugging and comparison."""
        return ("noop", self.reason)


class CreateIndexConcurrentlyOp(MigrateOperation):
    """Create an index with ``CREATE INDEX CONCURRENTLY``, outside the transaction of the migration.

    This class wraps the Alembic :class:`~alembic.operations.ops.CreateIndexOp`. It does not replace it. Thus the
    rendered call is still ``op.create_index(...)`` with the Alembic arguments. This class adds the
    ``postgresql_concurrently=True`` keyword. At render time, it also adds the ``autocommit_block()`` that the keyword
    needs. PostgreSQL refuses a concurrent index build inside a transaction block.
    """

    inner: CreateIndexOp

    def __init__(self, inner: CreateIndexOp) -> None:
        inner.kw["postgresql_concurrently"] = True
        self.inner = inner

    @override
    def reverse(self) -> DropIndexConcurrentlyOp:
        """Return the reverse operation, which drops the new index concurrently."""
        return DropIndexConcurrentlyOp(self.inner.reverse())

    @override
    def to_diff_tuple(self) -> tuple[str, object]:
        """Return a tuple equal to the Alembic ``add_index`` diff entry."""
        return ("add_index", self.inner.to_index())


class DropIndexConcurrentlyOp(MigrateOperation):
    """Drop an index with ``DROP INDEX CONCURRENTLY``, outside the transaction of the migration."""

    inner: DropIndexOp

    def __init__(self, inner: DropIndexOp) -> None:
        inner.kw["postgresql_concurrently"] = True
        self.inner = inner

    @override
    def reverse(self) -> CreateIndexConcurrentlyOp:
        """Return the reverse operation, which creates the dropped index again concurrently."""
        return CreateIndexConcurrentlyOp(self.inner.reverse())

    @override
    def to_diff_tuple(self) -> tuple[str, object]:
        """Return a tuple equal to the Alembic ``remove_index`` diff entry."""
        return ("remove_index", self.inner.to_index())


def _check_grant_group(grants: Sequence[GrantInfo]) -> tuple[GrantInfo, ...]:
    """Return *grants* as a tuple after a check that one ``GRANT`` or ``REVOKE`` statement can hold them all.

    Raises:
        ValueError: If *grants* is empty, or if two items differ in object, grantee, or ``grantable``.
    """
    if not grants:
        raise ValueError("A grant operation needs at least one privilege")
    first = grants[0]
    target = (first.schema, first.object_type, first.name, first.grantee, first.grantable)
    for grant in grants[1:]:
        if (grant.schema, grant.object_type, grant.name, grant.grantee, grant.grantable) != target:
            raise ValueError(f"A grant operation needs one object, grantee, and grant option: {grants!r}")
    return tuple(grants)


def _grant_group_key(grants: Sequence[GrantInfo]) -> tuple[str, str, str, str, tuple[str, ...]]:
    """Return ``(object_type, schema, name, grantee, privileges)`` for :meth:`MigrateOperation.to_diff_tuple`."""
    first = grants[0]
    return (first.object_type, first.schema, first.name, first.grantee, tuple(grant.privilege for grant in grants))
