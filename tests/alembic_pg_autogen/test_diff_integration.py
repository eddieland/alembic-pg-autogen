from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.engine import Engine

from alembic_pg_autogen import (
    Action,
    CanonicalState,
    canonicalize,
    diff,
    inspect_functions,
    inspect_triggers,
)


@pytest.fixture
def pg_conn(pg_engine: Engine) -> Generator[Connection]:
    """Provide an isolated connection that rolls back all DDL after each test."""
    with pg_engine.connect() as conn:
        txn = conn.begin()
        yield conn
        txn.rollback()


@pytest.mark.integration
class TestDiffIntegration:
    """4.1–4.4 — Full pipeline integration tests (inspect → canonicalize → diff)."""

    def test_function_drop(self, pg_conn: Connection):
        """4.1 — Function in DB but not in desired state produces DROP."""
        pg_conn.execute(text("CREATE FUNCTION public.existing_fn() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$"))
        current = CanonicalState(
            functions=inspect_functions(pg_conn, schemas=["public"]),
            triggers=inspect_triggers(pg_conn, schemas=["public"]),
        )
        # Desired state declares no managed objects — existing_fn should be dropped
        desired = CanonicalState(functions=(), triggers=())
        result = diff(current, desired)

        drop_ops = [op for op in result.function_ops if op.current and op.current.name == "existing_fn"]
        assert len(drop_ops) == 1
        assert drop_ops[0].action is Action.DROP
        assert drop_ops[0].desired is None

    def test_function_create(self, pg_conn: Connection):
        """4.2 — Function in desired state but not in DB produces CREATE."""
        current = CanonicalState(
            functions=inspect_functions(pg_conn, schemas=["public"]),
            triggers=inspect_triggers(pg_conn, schemas=["public"]),
        )
        desired = canonicalize(
            pg_conn,
            function_ddl=["CREATE FUNCTION public.new_fn() RETURNS integer LANGUAGE sql AS $$ SELECT 42 $$"],
            schemas=["public"],
        )
        result = diff(current, desired)

        create_ops = [op for op in result.function_ops if op.desired and op.desired.name == "new_fn"]
        assert len(create_ops) == 1
        assert create_ops[0].action is Action.CREATE
        assert create_ops[0].current is None

    def test_function_replace(self, pg_conn: Connection):
        """4.3 — Function exists in DB, desired has modified body produces REPLACE."""
        pg_conn.execute(text("CREATE FUNCTION public.mod_fn() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$"))
        current = CanonicalState(
            functions=inspect_functions(pg_conn, schemas=["public"]),
            triggers=inspect_triggers(pg_conn, schemas=["public"]),
        )
        desired = canonicalize(
            pg_conn,
            function_ddl=[
                "CREATE OR REPLACE FUNCTION public.mod_fn() RETURNS integer LANGUAGE sql AS $$ SELECT 999 $$"
            ],
            schemas=["public"],
        )
        result = diff(current, desired)

        replace_ops = [op for op in result.function_ops if op.current and op.current.name == "mod_fn"]
        assert len(replace_ops) == 1
        assert replace_ops[0].action is Action.REPLACE
        assert replace_ops[0].current is not None
        assert replace_ops[0].desired is not None
        assert "1" in replace_ops[0].current.definition
        assert "999" in replace_ops[0].desired.definition

    def test_trigger_create_drop_replace(self, pg_conn: Connection):
        """4.4 — Trigger create/drop/replace through the full pipeline."""
        # Set up: table, trigger function, and two existing triggers
        pg_conn.execute(text("CREATE TABLE public.diff_tbl (id integer PRIMARY KEY)"))
        pg_conn.execute(
            text(
                "CREATE FUNCTION public.diff_trg_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
            )
        )
        pg_conn.execute(
            text(
                "CREATE TRIGGER drop_me BEFORE INSERT ON public.diff_tbl "
                "FOR EACH ROW EXECUTE FUNCTION public.diff_trg_fn()"
            )
        )
        pg_conn.execute(
            text(
                "CREATE TRIGGER replace_me BEFORE INSERT ON public.diff_tbl "
                "FOR EACH ROW EXECUTE FUNCTION public.diff_trg_fn()"
            )
        )

        current = CanonicalState(
            functions=inspect_functions(pg_conn, schemas=["public"]),
            triggers=inspect_triggers(pg_conn, schemas=["public"]),
        )

        # Canonicalize desired DDL: function (unchanged), modified replace_me (AFTER), and new_trg.
        # canonicalize returns the full post-DDL catalog, so we filter to only declared triggers.
        canon = canonicalize(
            pg_conn,
            function_ddl=[
                "CREATE OR REPLACE FUNCTION public.diff_trg_fn() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
            ],
            trigger_ddl=[
                "CREATE OR REPLACE TRIGGER replace_me AFTER INSERT ON public.diff_tbl "
                "FOR EACH ROW EXECUTE FUNCTION public.diff_trg_fn()",
                "CREATE TRIGGER new_trg AFTER INSERT ON public.diff_tbl "
                "FOR EACH ROW EXECUTE FUNCTION public.diff_trg_fn()",
            ],
            schemas=["public"],
        )
        declared_triggers = {"replace_me", "new_trg"}
        desired = CanonicalState(
            functions=canon.functions,
            triggers=[t for t in canon.triggers if t.trigger_name in declared_triggers],
        )

        result = diff(current, desired)

        # drop_me in current but not desired → DROP
        drop_ops = [op for op in result.trigger_ops if op.action is Action.DROP]
        assert any(op.current and op.current.trigger_name == "drop_me" for op in drop_ops)

        # replace_me has different definition (BEFORE → AFTER) → REPLACE
        replace_ops = [op for op in result.trigger_ops if op.action is Action.REPLACE]
        assert any(op.current and op.current.trigger_name == "replace_me" for op in replace_ops)

        # new_trg in desired but not current → CREATE
        create_ops = [op for op in result.trigger_ops if op.action is Action.CREATE]
        assert any(op.desired and op.desired.trigger_name == "new_trg" for op in create_ops)
