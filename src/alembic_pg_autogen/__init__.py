"""Alembic autogenerate extension for PostgreSQL-specific objects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic.runtime.plugins import Plugin as _Plugin

import alembic_pg_autogen.compare as _compare_mod
import alembic_pg_autogen.compare_check_constraints as _compare_check_constraints_mod
import alembic_pg_autogen.compare_indexes as _compare_indexes_mod

# Imported for its side effect: the module body registers the ``renderers.dispatch_for`` handlers for every op in
# ``alembic_pg_autogen.ops``.  Without it Alembic raises "no dispatch function for object" while rendering the
# migration script.
import alembic_pg_autogen.render  # noqa: F401  # pyright: ignore[reportUnusedImport]
from alembic_pg_autogen.canonicalize import (
    CanonicalState,
    canonicalize,
    canonicalize_check_constraints,
    canonicalize_functions,
    canonicalize_indexes,
    canonicalize_triggers,
    canonicalize_views,
)
from alembic_pg_autogen.compare import SQLCreatable, setup
from alembic_pg_autogen.diff import Action, DiffResult, FunctionOp, TriggerOp, ViewOp, diff
from alembic_pg_autogen.inspect import (
    CheckConstraintInfo,
    FunctionInfo,
    IndexInfo,
    TriggerInfo,
    ViewInfo,
    current_schema,
    inspect_check_constraints,
    inspect_functions,
    inspect_indexes,
    inspect_triggers,
    inspect_views,
)
from alembic_pg_autogen.ops import (
    CreateFunctionOp,
    CreateIndexConcurrentlyOp,
    CreateTriggerOp,
    CreateViewOp,
    DropFunctionOp,
    DropIndexConcurrentlyOp,
    DropTriggerOp,
    DropViewOp,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
    ReplaceViewOp,
)
from alembic_pg_autogen.sentinels import IGNORED, Ignored

_Plugin.setup_plugin_from_module(_compare_mod, "alembic_pg_autogen.compare")
_Plugin.setup_plugin_from_module(_compare_check_constraints_mod, "alembic_pg_autogen.checkconstraints")
_Plugin.setup_plugin_from_module(_compare_indexes_mod, "alembic_pg_autogen.indexes")

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Final

__all__: Final[Sequence[str]] = [
    "Action",
    "CanonicalState",
    "CheckConstraintInfo",
    "CreateFunctionOp",
    "CreateIndexConcurrentlyOp",
    "CreateTriggerOp",
    "CreateViewOp",
    "DiffResult",
    "DropFunctionOp",
    "DropIndexConcurrentlyOp",
    "DropTriggerOp",
    "DropViewOp",
    "FunctionInfo",
    "FunctionOp",
    "IGNORED",
    "Ignored",
    "IndexInfo",
    "ReplaceFunctionOp",
    "ReplaceTriggerOp",
    "ReplaceViewOp",
    "SQLCreatable",
    "TriggerInfo",
    "TriggerOp",
    "ViewInfo",
    "ViewOp",
    "canonicalize",
    "canonicalize_check_constraints",
    "canonicalize_functions",
    "canonicalize_indexes",
    "canonicalize_triggers",
    "canonicalize_views",
    "current_schema",
    "diff",
    "inspect_check_constraints",
    "inspect_functions",
    "inspect_indexes",
    "inspect_triggers",
    "inspect_views",
    "setup",
]
