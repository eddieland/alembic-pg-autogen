"""Alembic autogenerate extension for PostgreSQL-specific objects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic.runtime.plugins import Plugin as _Plugin

import alembic_pg_autogen._compare as _compare_mod  # noqa: F401  # pyright: ignore[reportUnusedImport]
import alembic_pg_autogen._render as _render  # noqa: F401  # pyright: ignore[reportUnusedImport]
from alembic_pg_autogen._canonicalize import (
    CanonicalState,
    canonicalize,
    canonicalize_functions,
    canonicalize_triggers,
)
from alembic_pg_autogen._compare import SQLCreatable, setup
from alembic_pg_autogen._diff import Action, DiffResult, FunctionOp, TriggerOp, diff
from alembic_pg_autogen._inspect import FunctionInfo, TriggerInfo, inspect_functions, inspect_triggers
from alembic_pg_autogen._ops import (
    CreateFunctionOp,
    CreateTriggerOp,
    DropFunctionOp,
    DropTriggerOp,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
)

_Plugin.setup_plugin_from_module(_compare_mod, "alembic_pg_autogen.compare")

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Final

__all__: Final[Sequence[str]] = [
    "Action",
    "CanonicalState",
    "CreateFunctionOp",
    "CreateTriggerOp",
    "DiffResult",
    "DropFunctionOp",
    "DropTriggerOp",
    "FunctionInfo",
    "FunctionOp",
    "ReplaceFunctionOp",
    "ReplaceTriggerOp",
    "SQLCreatable",
    "TriggerInfo",
    "TriggerOp",
    "canonicalize",
    "canonicalize_functions",
    "canonicalize_triggers",
    "diff",
    "inspect_functions",
    "inspect_triggers",
    "setup",
]
