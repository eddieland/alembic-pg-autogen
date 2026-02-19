"""Alembic autogenerate extension for PostgreSQL-specific objects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic_pg_autogen._inspect import FunctionInfo, TriggerInfo, inspect_functions, inspect_triggers

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Final

__all__: Final[Sequence[str]] = [
    "FunctionInfo",
    "TriggerInfo",
    "inspect_functions",
    "inspect_triggers",
]
