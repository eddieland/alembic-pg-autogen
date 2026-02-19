"""Diff logic for comparing canonical catalog snapshots."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Sequence

    from alembic_pg_autogen._canonicalize import CanonicalState
    from alembic_pg_autogen._inspect import FunctionInfo, TriggerInfo


class Action(enum.Enum):
    """The kind of operation needed to reconcile current state with desired state."""

    CREATE = "create"
    REPLACE = "replace"
    DROP = "drop"


class FunctionOp(NamedTuple):
    """A single diff operation on a PostgreSQL function."""

    action: Action
    current: FunctionInfo | None
    desired: FunctionInfo | None


class TriggerOp(NamedTuple):
    """A single diff operation on a PostgreSQL trigger."""

    action: Action
    current: TriggerInfo | None
    desired: TriggerInfo | None


class DiffResult(NamedTuple):
    """Result of comparing two canonical catalog snapshots."""

    function_ops: Sequence[FunctionOp]
    trigger_ops: Sequence[TriggerOp]


def diff(current: CanonicalState, desired: CanonicalState) -> DiffResult:
    """Compare two canonical catalog snapshots and produce diff operations.

    Matches functions by ``(schema, name, identity_args)`` and triggers by
    ``(schema, table_name, trigger_name)``.  Objects present only in *desired*
    produce ``CREATE`` ops, objects only in *current* produce ``DROP`` ops, and
    objects in both with differing definitions produce ``REPLACE`` ops.

    Args:
        current: The current database state (from inspection).
        desired: The desired state (from canonicalization).

    Returns:
        A :class:`DiffResult` with sorted sequences of function and trigger ops.
    """
    return DiffResult(
        function_ops=_diff_functions(current.functions, desired.functions),
        trigger_ops=_diff_triggers(current.triggers, desired.triggers),
    )


def _diff_functions(
    current_items: Sequence[FunctionInfo],
    desired_items: Sequence[FunctionInfo],
) -> list[FunctionOp]:
    """Diff two sequences of FunctionInfo by identity key (first 3 fields)."""
    current_by_key = {item[:3]: item for item in current_items}
    desired_by_key = {item[:3]: item for item in desired_items}

    ops: list[FunctionOp] = []
    for key in sorted(current_by_key.keys() | desired_by_key.keys()):
        cur = current_by_key.get(key)
        des = desired_by_key.get(key)
        if cur is None:
            ops.append(FunctionOp(action=Action.CREATE, current=None, desired=des))
        elif des is None:
            ops.append(FunctionOp(action=Action.DROP, current=cur, desired=None))
        elif cur.definition != des.definition:
            ops.append(FunctionOp(action=Action.REPLACE, current=cur, desired=des))

    return ops


def _diff_triggers(
    current_items: Sequence[TriggerInfo],
    desired_items: Sequence[TriggerInfo],
) -> list[TriggerOp]:
    """Diff two sequences of TriggerInfo by identity key (first 3 fields)."""
    current_by_key = {item[:3]: item for item in current_items}
    desired_by_key = {item[:3]: item for item in desired_items}

    ops: list[TriggerOp] = []
    for key in sorted(current_by_key.keys() | desired_by_key.keys()):
        cur = current_by_key.get(key)
        des = desired_by_key.get(key)
        if cur is None:
            ops.append(TriggerOp(action=Action.CREATE, current=None, desired=des))
        elif des is None:
            ops.append(TriggerOp(action=Action.DROP, current=cur, desired=None))
        elif cur.definition != des.definition:
            ops.append(TriggerOp(action=Action.REPLACE, current=cur, desired=des))

    return ops
