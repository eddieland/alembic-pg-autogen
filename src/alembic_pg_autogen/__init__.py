"""Alembic autogenerate extension for PostgreSQL-specific objects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic.runtime.plugins import Plugin as _Plugin

import alembic_pg_autogen.compare as _compare_mod

# Imported for its side effect: the module body registers the ``renderers.dispatch_for`` handlers for every op in
# ``alembic_pg_autogen.ops``.  Without it Alembic raises "no dispatch function for object" while rendering the
# migration script.
import alembic_pg_autogen.render  # noqa: F401  # pyright: ignore[reportUnusedImport]
from alembic_pg_autogen.canonicalize import (
    CanonicalState,
    canonicalize,
    canonicalize_functions,
    canonicalize_triggers,
    canonicalize_views,
)
from alembic_pg_autogen.compare import SQLCreatable, setup
from alembic_pg_autogen.diff import Action, DiffResult, FunctionOp, TriggerOp, ViewOp, diff
from alembic_pg_autogen.inspect import (
    FunctionInfo,
    TriggerInfo,
    ViewInfo,
    inspect_functions,
    inspect_triggers,
    inspect_views,
)
from alembic_pg_autogen.ops import (
    CreateFunctionOp,
    CreateTriggerOp,
    CreateViewOp,
    DropFunctionOp,
    DropTriggerOp,
    DropViewOp,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
    ReplaceViewOp,
)
from alembic_pg_autogen.sentinels import IGNORED, Ignored

_Plugin.setup_plugin_from_module(_compare_mod, "alembic_pg_autogen.compare")

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Final

__all__: Final[Sequence[str]] = [
    "Action",
    "CanonicalState",
    "CreateFunctionOp",
    "CreateTriggerOp",
    "CreateViewOp",
    "DiffResult",
    "DropFunctionOp",
    "DropTriggerOp",
    "DropViewOp",
    "FunctionInfo",
    "FunctionOp",
    "IGNORED",
    "Ignored",
    "ReplaceFunctionOp",
    "ReplaceTriggerOp",
    "ReplaceViewOp",
    "SQLCreatable",
    "TriggerInfo",
    "TriggerOp",
    "ViewInfo",
    "ViewOp",
    "canonicalize",
    "canonicalize_functions",
    "canonicalize_triggers",
    "canonicalize_views",
    "diff",
    "inspect_functions",
    "inspect_triggers",
    "inspect_views",
    "setup",
]
