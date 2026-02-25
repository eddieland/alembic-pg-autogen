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
    import postgast

    return _render_execute(postgast.to_drop(op.current.definition))


@renderers.dispatch_for(CreateTriggerOp)
def _render_create_trigger(_autogen_context: AutogenContext, op: CreateTriggerOp) -> str:
    """Render a CREATE TRIGGER via op.execute()."""
    return _render_execute(op.desired.definition)


@renderers.dispatch_for(ReplaceTriggerOp)
def _render_replace_trigger(_autogen_context: AutogenContext, op: ReplaceTriggerOp) -> list[str]:
    """Render DROP TRIGGER + CREATE TRIGGER via two op.execute() calls."""
    import postgast

    drop = _render_execute(postgast.to_drop(op.current.definition))
    create = _render_execute(op.desired.definition)
    return [drop, create]


@renderers.dispatch_for(DropTriggerOp)
def _render_drop_trigger(_autogen_context: AutogenContext, op: DropTriggerOp) -> str:
    """Render a DROP TRIGGER via op.execute()."""
    import postgast

    return _render_execute(postgast.to_drop(op.current.definition))


def _render_execute(ddl: str) -> str:
    """Wrap a DDL string in an ``op.execute(...)`` call with safe quoting."""
    return f"op.execute({_quote_ddl(ddl)})"


def _quote_ddl(ddl: str) -> str:
    r"""Quote a DDL string for inclusion in generated Python source.

    Alembic's ``_indent()`` adds leading spaces to *every* line of the rendered op text
    (``re.sub(r"^", "    ", text, flags=re.M)``).  Multi-line triple-quoted string literals
    would absorb that indentation into the string value, silently corrupting the DDL.

    To avoid this, multi-line DDL always uses ``repr()`` which escapes newlines as ``\n``,
    producing a single-line literal that is immune to re-indentation.  Single-line DDL may
    use triple-quoting for readability when it contains quotes or backslashes.
    """
    # Multi-line DDL must use repr() to keep the literal on one line.
    if "\n" in ddl:
        return repr(ddl)
    if "'" not in ddl and "\\" not in ddl:
        return repr(ddl)
    # Single-line DDL with quotes: triple-quoting avoids backslash escapes.
    if "'''" not in ddl and '"""' not in ddl:
        if "\\" in ddl:
            return f"r'''{ddl}'''"
        return f"'''{ddl}'''"
    # Fallback: repr handles all edge cases (escapes quotes and backslashes).
    return repr(ddl)
