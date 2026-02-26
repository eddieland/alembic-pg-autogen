"""Custom MigrateOperation subclasses for PostgreSQL objects."""

# ruff: noqa: D107  # __init__ signatures are self-documenting; class docstrings suffice.

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic.operations.ops import MigrateOperation
from typing_extensions import override

if TYPE_CHECKING:
    from alembic_pg_autogen.inspect import FunctionInfo, TriggerInfo


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
