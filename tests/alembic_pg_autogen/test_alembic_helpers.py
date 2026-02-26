from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from alembic_pg_autogen.inspect import inspect_functions, inspect_triggers

if TYPE_CHECKING:
    from .alembic_helpers import AlembicProject


@pytest.mark.integration
def test_execute_and_inspect_function(alembic_project: AlembicProject):
    alembic_project.execute("""
        CREATE FUNCTION hello() RETURNS text
        LANGUAGE sql AS $$ SELECT 'hello'::text $$
    """)

    with alembic_project.connect() as conn:
        funcs = inspect_functions(conn, schemas=[alembic_project.schema])

    assert len(funcs) == 1
    assert funcs[0].name == "hello"
    assert funcs[0].schema == alembic_project.schema


@pytest.mark.integration
def test_execute_and_inspect_trigger(alembic_project: AlembicProject):
    alembic_project.execute("CREATE TABLE t (id int)")
    alembic_project.execute("""
        CREATE FUNCTION trg_fn() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$
    """)
    alembic_project.execute("""
        CREATE TRIGGER my_trigger BEFORE INSERT ON t
        FOR EACH ROW EXECUTE FUNCTION trg_fn()
    """)

    with alembic_project.connect() as conn:
        triggers = inspect_triggers(conn, schemas=[alembic_project.schema])

    assert len(triggers) == 1
    assert triggers[0].trigger_name == "my_trigger"
    assert triggers[0].table_name == "t"
    assert triggers[0].schema == alembic_project.schema


@pytest.mark.integration
def test_schema_isolation(alembic_project: AlembicProject):
    alembic_project.execute("""
        CREATE FUNCTION isolated_fn() RETURNS void
        LANGUAGE sql AS $$ $$
    """)

    with alembic_project.connect() as conn:
        all_funcs = inspect_functions(conn)

    schemas = {f.schema for f in all_funcs}
    assert alembic_project.schema in schemas
    own_funcs = [f for f in all_funcs if f.schema == alembic_project.schema]
    assert len(own_funcs) == 1
    assert own_funcs[0].name == "isolated_fn"
