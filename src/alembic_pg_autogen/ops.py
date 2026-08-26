"""Custom MigrateOperation subclasses for PostgreSQL objects."""

# ruff: noqa: D107  # __init__ signatures are self-documenting; class docstrings suffice.

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic.operations.ops import MigrateOperation
from typing_extensions import override

if TYPE_CHECKING:
    from alembic.operations.ops import CreateIndexOp, DropIndexOp

    from alembic_pg_autogen.inspect import FunctionInfo, TriggerInfo, ViewInfo


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


class CreateIndexConcurrentlyOp(MigrateOperation):
    """Create an index with ``CREATE INDEX CONCURRENTLY``, outside the migration's transaction.

    Wraps Alembic's own :class:`~alembic.operations.ops.CreateIndexOp` rather than replacing it, so the rendered call
    is still ``op.create_index(...)`` with the arguments Alembic would have produced. What this op adds is the
    ``postgresql_concurrently=True`` keyword and, at render time, the ``autocommit_block()`` that keyword requires:
    PostgreSQL refuses to build an index concurrently inside a transaction block.
    """

    inner: CreateIndexOp

    def __init__(self, inner: CreateIndexOp) -> None:
        inner.kw["postgresql_concurrently"] = True
        self.inner = inner

    @override
    def reverse(self) -> DropIndexConcurrentlyOp:
        """Reverse is dropping the newly created index, also concurrently."""
        return DropIndexConcurrentlyOp(self.inner.reverse())

    @override
    def to_diff_tuple(self) -> tuple[str, object]:
        """Return a tuple mirroring Alembic's own ``add_index`` diff entry."""
        return ("add_index", self.inner.to_index())


class DropIndexConcurrentlyOp(MigrateOperation):
    """Drop an index with ``DROP INDEX CONCURRENTLY``, outside the migration's transaction."""

    inner: DropIndexOp

    def __init__(self, inner: DropIndexOp) -> None:
        inner.kw["postgresql_concurrently"] = True
        self.inner = inner

    @override
    def reverse(self) -> CreateIndexConcurrentlyOp:
        """Reverse is recreating the dropped index, also concurrently."""
        return CreateIndexConcurrentlyOp(self.inner.reverse())

    @override
    def to_diff_tuple(self) -> tuple[str, object]:
        """Return a tuple mirroring Alembic's own ``remove_index`` diff entry."""
        return ("remove_index", self.inner.to_index())
