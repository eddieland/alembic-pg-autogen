"""Render functions for emitting migration code for PostgreSQL objects."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic.autogenerate.render import renderers

from alembic_pg_autogen._ops import (
    CreateFunctionOp,
    CreateTriggerOp,
    DropFunctionOp,
    DropTriggerOp,
    ReplaceFunctionOp,
    ReplaceTriggerOp,
)

if TYPE_CHECKING:
    from alembic.autogenerate.api import AutogenContext


@renderers.dispatch_for(CreateFunctionOp)
def _render_create_function(_autogen_context: AutogenContext, op: CreateFunctionOp) -> str:
    """Render a CREATE OR REPLACE FUNCTION via op.execute()."""
    return _render_execute(op.desired.definition)


@renderers.dispatch_for(ReplaceFunctionOp)
def _render_replace_function(_autogen_context: AutogenContext, op: ReplaceFunctionOp) -> str:
    """Render a CREATE OR REPLACE FUNCTION (replace) via op.execute()."""
    return _render_execute(op.desired.definition)


@renderers.dispatch_for(DropFunctionOp)
def _render_drop_function(_autogen_context: AutogenContext, op: DropFunctionOp) -> str:
    """Render a DROP FUNCTION via op.execute()."""
    info = op.current
    return f'op.execute("DROP FUNCTION {info.schema}.{info.name}({info.identity_args})")'


@renderers.dispatch_for(CreateTriggerOp)
def _render_create_trigger(_autogen_context: AutogenContext, op: CreateTriggerOp) -> str:
    """Render a CREATE TRIGGER via op.execute()."""
    return _render_execute(op.desired.definition)


@renderers.dispatch_for(ReplaceTriggerOp)
def _render_replace_trigger(_autogen_context: AutogenContext, op: ReplaceTriggerOp) -> list[str]:
    """Render DROP TRIGGER + CREATE TRIGGER via two op.execute() calls."""
    info = op.current
    drop = f'op.execute("DROP TRIGGER {info.trigger_name} ON {info.schema}.{info.table_name}")'
    create = _render_execute(op.desired.definition)
    return [drop, create]


@renderers.dispatch_for(DropTriggerOp)
def _render_drop_trigger(_autogen_context: AutogenContext, op: DropTriggerOp) -> str:
    """Render a DROP TRIGGER via op.execute()."""
    info = op.current
    return f'op.execute("DROP TRIGGER {info.trigger_name} ON {info.schema}.{info.table_name}")'


def _render_execute(ddl: str) -> str:
    """Wrap a DDL string in an ``op.execute(...)`` call with safe quoting."""
    return f"op.execute({_quote_ddl(ddl)})"


def _quote_ddl(ddl: str) -> str:
    """Quote a DDL string for inclusion in generated Python source.

    Uses triple-quoting with a raw prefix when the DDL contains single quotes
    or backslashes, and simple repr quoting otherwise.
    """
    if "'" not in ddl and "\\" not in ddl:
        return repr(ddl)
    # Triple-quoted string avoids escaping single quotes.  Use a raw string
    # (r-prefix) only when backslashes are present but no triple-quote
    # sequences exist in the DDL itself.
    if "'''" not in ddl and '"""' not in ddl:
        if "\\" in ddl:
            return f"r'''{ddl}'''"
        return f"'''{ddl}'''"
    # Fallback: repr handles all edge cases (escapes quotes and backslashes).
    return repr(ddl)
