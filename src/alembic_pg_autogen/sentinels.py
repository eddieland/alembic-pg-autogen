"""Sentinel values accepted by the desired-state configuration keys."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Literal

from typing_extensions import override

if TYPE_CHECKING:
    from typing import Final, TypeAlias


class _IgnoredSentinel(enum.Enum):
    """Backing enum for :data:`IGNORED`.

    A single-member enum is used rather than a bare object so that type checkers can narrow
    ``value is IGNORED`` through the :data:`Ignored` literal alias.
    """

    IGNORED = "IGNORED"

    @override
    def __repr__(self) -> str:
        """Return ``IGNORED`` so log output and tracebacks read naturally."""
        return "IGNORED"

    @override
    def __str__(self) -> str:
        """Return ``IGNORED`` so formatted messages read naturally."""
        return "IGNORED"


IGNORED: Final = _IgnoredSentinel.IGNORED
"""Marks a PostgreSQL object type as unmanaged.

Pass this instead of a DDL sequence to ``pg_functions``, ``pg_views``, or ``pg_triggers`` to leave that object type
entirely alone: nothing is inspected, nothing is diffed, and no operations are emitted for it — in particular, existing
objects of that type are **not** dropped.

This differs from passing an empty sequence, which declares "there should be no objects of this type" and therefore
drops every existing one::

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        pg_functions=PG_FUNCTIONS,
        pg_triggers=PG_TRIGGERS,
        pg_views=IGNORED,  # views are managed by hand for now
    )
"""

Ignored: TypeAlias = Literal[_IgnoredSentinel.IGNORED]
"""Type of the :data:`IGNORED` sentinel, for annotating configuration values."""
