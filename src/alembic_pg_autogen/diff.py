"""Diff logic for comparing canonical catalog snapshots."""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING, NamedTuple, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from alembic_pg_autogen.canonicalize import CanonicalState
    from alembic_pg_autogen.inspect import FunctionInfo, GrantInfo, TriggerInfo, ViewInfo

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


class ViewOp(NamedTuple):
    """A single diff operation on a PostgreSQL view."""

    action: Action
    current: ViewInfo | None
    desired: ViewInfo | None


class GrantOp(NamedTuple):
    """A single diff operation on one privilege of one grantee.

    ``CREATE`` grants the privilege and ``DROP`` revokes it. ``REPLACE`` means that only ``grantable`` differs, so the
    grant option must be added or removed.
    """

    action: Action
    current: GrantInfo | None
    desired: GrantInfo | None


class DiffResult(NamedTuple):
    """Result of comparing two canonical catalog snapshots."""

    function_ops: Sequence[FunctionOp]
    trigger_ops: Sequence[TriggerOp]
    view_ops: Sequence[ViewOp] = ()


_InfoT = TypeVar("_InfoT", "FunctionInfo", "TriggerInfo", "ViewInfo", "GrantInfo")
_OpT = TypeVar("_OpT", FunctionOp, TriggerOp, ViewOp, GrantOp)


def diff(current: CanonicalState, desired: CanonicalState) -> DiffResult:
    """Compare two canonical catalog snapshots and produce diff operations.

    Matches functions by ``(schema, name, identity_args)``, triggers by ``(schema, table_name, trigger_name)``, and
    views by ``(schema, name)``.  Objects present only in *desired* produce ``CREATE`` ops, objects only in *current*
    produce ``DROP`` ops, and objects in both with differing definitions produce ``REPLACE`` ops.  Grants use
    :func:`diff_grants`.

    Args:
        current: The current database state (from inspection).
        desired: The desired state (from canonicalization).

    Returns:
        A :class:`DiffResult` with sorted sequences of function, trigger, and view ops.
    """
    result = DiffResult(
        function_ops=_diff_items(current.functions, desired.functions, FunctionOp),
        trigger_ops=_diff_items(current.triggers, desired.triggers, TriggerOp),
        view_ops=_diff_items(current.views, desired.views, ViewOp),
    )
    log.debug(
        "Diff produced %d function ops, %d trigger ops, and %d view ops",
        len(result.function_ops),
        len(result.trigger_ops),
        len(result.view_ops),
    )
    return result


def diff_grants(current: Sequence[GrantInfo], desired: Sequence[GrantInfo]) -> Sequence[GrantOp]:
    """Compare two sets of grants and produce one :class:`GrantOp` per differing privilege.

    Grants match by ``(schema, object_type, name, grantee, privilege)``.  A privilege only in *desired* gives
    ``CREATE``, a privilege only in *current* gives ``DROP``, and a privilege on both sides with a different
    ``grantable`` flag gives ``REPLACE``.

    Grants have their own function, not a field of :class:`DiffResult`.  A new field would break code that unpacks a
    ``DiffResult`` or a :class:`~alembic_pg_autogen.canonicalize.CanonicalState` into three names.

    Args:
        current: The grants in the database, from :func:`~alembic_pg_autogen.inspect.inspect_grants`.
        desired: The declared grants, from :func:`~alembic_pg_autogen.canonicalize.canonicalize_grants`.

    Returns:
        The grant ops, sorted by identity.
    """
    ops = _diff_items(current, desired, GrantOp)
    log.debug("Diff produced %d grant ops", len(ops))
    return ops


def _diff_items(
    current_items: Sequence[_InfoT],
    desired_items: Sequence[_InfoT],
    make_op: Callable[[Action, _InfoT | None, _InfoT | None], _OpT],
) -> list[_OpT]:
    """Diff two sequences of catalog items by identity key (all fields except the last one).

    The last field is the payload: ``definition`` for functions, triggers, and views, and ``grantable`` for grants.
    The identity of two matched items is equal, so the items differ exactly when their payloads differ.
    """
    current_by_key = {item[:-1]: item for item in current_items}
    desired_by_key = {item[:-1]: item for item in desired_items}

    ops: list[_OpT] = []
    for key in sorted(current_by_key.keys() | desired_by_key.keys()):
        cur = current_by_key.get(key)
        des = desired_by_key.get(key)
        if cur is None:
            ops.append(make_op(Action.CREATE, None, des))
        elif des is None:
            ops.append(make_op(Action.DROP, cur, None))
        elif cur != des:
            ops.append(make_op(Action.REPLACE, cur, des))

    return ops
