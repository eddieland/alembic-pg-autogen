"""Diff logic for comparing canonical catalog snapshots."""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING, NamedTuple, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from alembic_pg_autogen.canonicalize import CanonicalState
    from alembic_pg_autogen.inspect import FunctionInfo, TriggerInfo

log = logging.getLogger(__name__)


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


_InfoT = TypeVar("_InfoT", "FunctionInfo", "TriggerInfo")
_OpT = TypeVar("_OpT", FunctionOp, TriggerOp)


def diff(current: CanonicalState, desired: CanonicalState) -> DiffResult:
    """Compare two canonical catalog snapshots and produce diff operations.

    Matches functions by ``(schema, name, identity_args)`` and triggers by ``(schema, table_name, trigger_name)``.
    Objects present only in *desired* produce ``CREATE`` ops, objects only in *current* produce ``DROP`` ops, and
    objects in both with differing definitions produce ``REPLACE`` ops.

    Args:
        current: The current database state (from inspection).
        desired: The desired state (from canonicalization).

    Returns:
        A :class:`DiffResult` with sorted sequences of function and trigger ops.
    """
    result = DiffResult(
        function_ops=_diff_items(current.functions, desired.functions, FunctionOp),
        trigger_ops=_diff_items(current.triggers, desired.triggers, TriggerOp),
    )
    log.debug("Diff produced %d function ops and %d trigger ops", len(result.function_ops), len(result.trigger_ops))
    return result


def _diff_items(
    current_items: Sequence[_InfoT],
    desired_items: Sequence[_InfoT],
    make_op: Callable[[Action, _InfoT | None, _InfoT | None], _OpT],
) -> list[_OpT]:
    """Diff two sequences of catalog items by identity key (first 3 fields)."""
    current_by_key = {item[:3]: item for item in current_items}
    desired_by_key = {item[:3]: item for item in desired_items}

    ops: list[_OpT] = []
    for key in sorted(current_by_key.keys() | desired_by_key.keys()):
        cur = current_by_key.get(key)
        des = desired_by_key.get(key)
        if cur is None:
            ops.append(make_op(Action.CREATE, None, des))
        elif des is None:
            ops.append(make_op(Action.DROP, cur, None))
        elif cur.definition != des.definition:
            ops.append(make_op(Action.REPLACE, cur, des))

    return ops
