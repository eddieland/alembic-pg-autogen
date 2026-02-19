"""End-to-end integration tests for realistic audit trigger patterns."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic.command import downgrade, revision, upgrade
from sqlalchemy import MetaData

from alembic_pg_autogen import inspect_functions, inspect_triggers

if TYPE_CHECKING:
    from .alembic_helpers import AlembicProject

TABLES = ("users", "orders", "payments", "products", "inventory")


def _autogenerate(project: AlembicProject, **attrs: object) -> str:
    """Run autogenerate and return the generated migration file content."""
    cfg = project.config
    for key, value in attrs.items():
        cfg.attributes[key] = value

    script = revision(cfg, message="test", autogenerate=True)
    assert script is not None

    versions_dir = Path(cfg.get_main_option("script_location")) / "versions"  # pyright: ignore[reportArgumentType]
    migration_files = sorted(versions_dir.glob("*.py"))
    return migration_files[-1].read_text()


def _setup_tables(project: AlembicProject, tables: tuple[str, ...] = TABLES) -> None:
    """Create the audit_log table and subject tables in the project schema."""
    schema = project.schema
    project.execute(f"""\
        CREATE TABLE {schema}.audit_log (
            id serial PRIMARY KEY,
            table_name text NOT NULL,
            action text NOT NULL,
            row_data jsonb,
            changed_at timestamptz DEFAULT now()
        )
    """)
    for table in tables:
        project.execute(f"CREATE TABLE {schema}.{table} (id serial PRIMARY KEY, name text)")


def _shape_a_function(schema: str, table: str) -> str:
    """Return a per-table audit function DDL (Shape A) with SECURITY DEFINER."""
    return f"""\
CREATE OR REPLACE FUNCTION {schema}.audit_{table}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    INSERT INTO {schema}.audit_log (table_name, action, row_data)
    VALUES ('{table}', TG_OP, row_to_json(NEW));
    RETURN NEW;
END;
$$"""


def _shape_a_trigger(schema: str, table: str) -> str:
    """Return a per-table audit trigger DDL (Shape A)."""
    return f"""\
CREATE OR REPLACE TRIGGER audit_{table}_trg
AFTER INSERT OR UPDATE ON {schema}.{table}
FOR EACH ROW EXECUTE FUNCTION {schema}.audit_{table}()"""


def _shared_audit_function(schema: str) -> str:
    """Return a shared audit function DDL (Shape B) with SECURITY DEFINER."""
    return f"""\
CREATE OR REPLACE FUNCTION {schema}.audit_fn()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    INSERT INTO {schema}.audit_log (table_name, action, row_data)
    VALUES (TG_TABLE_NAME, TG_OP, row_to_json(NEW));
    RETURN NEW;
END;
$$"""


def _shape_b_trigger(schema: str, table: str) -> str:
    """Return a per-table trigger referencing the shared audit function (Shape B)."""
    return f"""\
CREATE OR REPLACE TRIGGER audit_{table}_trg
AFTER INSERT OR UPDATE ON {schema}.{table}
FOR EACH ROW EXECUTE FUNCTION {schema}.audit_fn()"""


def _reflected_metadata(project: AlembicProject) -> MetaData:
    """Reflect table metadata from the database (as a user with ORM models would provide)."""
    metadata = MetaData()
    with project.connect() as conn:
        metadata.reflect(bind=conn)
    return metadata


def _upgrade_body(content: str) -> str:
    """Extract the upgrade() function body from migration content."""
    match = re.search(r"def upgrade\(\).*?(?=def downgrade\(\))", content, re.DOTALL)
    assert match is not None
    return match.group(0)


def _count_occurrences(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text, re.IGNORECASE))


@pytest.mark.integration
class TestShapeAInitialCreation:
    """2.1 — Shape A: initial creation of per-table audit functions and triggers."""

    def test_creates_all_functions_and_triggers(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        fn_ddl = [_shape_a_function(schema, t) for t in TABLES]
        trg_ddl = [_shape_a_trigger(schema, t) for t in TABLES]

        content = _autogenerate(alembic_project, pg_functions=fn_ddl, pg_triggers=trg_ddl)
        body = _upgrade_body(content)

        assert _count_occurrences(body, r"CREATE OR REPLACE FUNCTION") == 5
        assert _count_occurrences(body, r"CREATE TRIGGER") == 5

        # Functions must appear before triggers in upgrade
        last_fn = max(body.lower().find(f"audit_{t}()") for t in TABLES if body.lower().find(f"audit_{t}()") != -1)
        first_trg = min(
            body.lower().find(f"audit_{t}_trg") for t in TABLES if body.lower().find(f"audit_{t}_trg") != -1
        )
        assert last_fn < first_trg, "All function creates should precede trigger creates"


@pytest.mark.integration
class TestShapeANoOp:
    """2.2 — Shape A: no-op when database matches desired state."""

    def test_matching_state_produces_no_ops(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        for t in TABLES:
            alembic_project.execute(_shape_a_function(schema, t))
            alembic_project.execute(_shape_a_trigger(schema, t))

        fn_ddl = [_shape_a_function(schema, t) for t in TABLES]
        trg_ddl = [_shape_a_trigger(schema, t) for t in TABLES]

        content = _autogenerate(alembic_project, pg_functions=fn_ddl, pg_triggers=trg_ddl)
        body = _upgrade_body(content)
        assert "op.execute(" not in body


@pytest.mark.integration
class TestShapeAFunctionModification:
    """2.3 — Shape A: modifying one function body produces exactly one replace."""

    def test_single_function_change(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        for t in TABLES:
            alembic_project.execute(_shape_a_function(schema, t))
            alembic_project.execute(_shape_a_trigger(schema, t))

        # Modify only the 'orders' function to also capture the action timestamp
        modified_fn = f"""\
CREATE OR REPLACE FUNCTION {schema}.audit_orders()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    INSERT INTO {schema}.audit_log (table_name, action, row_data, changed_at)
    VALUES ('orders', TG_OP, row_to_json(NEW), now());
    RETURN NEW;
END;
$$"""
        fn_ddl = [modified_fn if t == "orders" else _shape_a_function(schema, t) for t in TABLES]
        trg_ddl = [_shape_a_trigger(schema, t) for t in TABLES]

        content = _autogenerate(alembic_project, pg_functions=fn_ddl, pg_triggers=trg_ddl)
        body = _upgrade_body(content)

        assert _count_occurrences(body, r"CREATE OR REPLACE FUNCTION") == 1
        assert "audit_orders" in body
        assert "CREATE TRIGGER" not in body.upper() or _count_occurrences(body, r"CREATE TRIGGER") == 0


@pytest.mark.integration
class TestShapeBInitialCreation:
    """3.1 — Shape B: initial creation with shared function and per-table triggers."""

    def test_creates_shared_function_and_triggers(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        fn_ddl = [_shared_audit_function(schema)]
        trg_ddl = [_shape_b_trigger(schema, t) for t in TABLES]

        content = _autogenerate(alembic_project, pg_functions=fn_ddl, pg_triggers=trg_ddl)
        body = _upgrade_body(content)

        assert _count_occurrences(body, r"CREATE OR REPLACE FUNCTION") == 1
        assert _count_occurrences(body, r"CREATE TRIGGER") == 5

        fn_pos = body.lower().find("audit_fn")
        first_trg = min(
            body.lower().find(f"audit_{t}_trg") for t in TABLES if body.lower().find(f"audit_{t}_trg") != -1
        )
        assert fn_pos < first_trg, "Shared function create should precede trigger creates"


@pytest.mark.integration
class TestShapeBAddTrigger:
    """3.2 — Shape B: adding a trigger for a new table."""

    def test_adds_one_trigger_only(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        # Set up existing state with shared function + 5 triggers
        alembic_project.execute(_shared_audit_function(schema))
        for t in TABLES:
            alembic_project.execute(_shape_b_trigger(schema, t))

        # Add a 6th table and trigger
        alembic_project.execute(f"CREATE TABLE {schema}.shipments (id serial PRIMARY KEY, name text)")

        fn_ddl = [_shared_audit_function(schema)]
        trg_ddl = [_shape_b_trigger(schema, t) for t in (*TABLES, "shipments")]

        content = _autogenerate(alembic_project, pg_functions=fn_ddl, pg_triggers=trg_ddl)
        body = _upgrade_body(content)

        assert _count_occurrences(body, r"CREATE TRIGGER") == 1
        assert "shipments" in body
        assert "CREATE OR REPLACE FUNCTION" not in body.upper()


@pytest.mark.integration
class TestShapeBRemoveTrigger:
    """3.3 — Shape B: removing a trigger when a table is dropped."""

    def test_drops_one_trigger_only(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        alembic_project.execute(_shared_audit_function(schema))
        for t in TABLES:
            alembic_project.execute(_shape_b_trigger(schema, t))

        # Desired state excludes 'inventory' trigger (4 triggers)
        remaining = tuple(t for t in TABLES if t != "inventory")
        fn_ddl = [_shared_audit_function(schema)]
        trg_ddl = [_shape_b_trigger(schema, t) for t in remaining]

        content = _autogenerate(alembic_project, pg_functions=fn_ddl, pg_triggers=trg_ddl)
        body = _upgrade_body(content)

        assert _count_occurrences(body, r"DROP TRIGGER") == 1
        assert "inventory" in body
        assert "CREATE OR REPLACE FUNCTION" not in body.upper()
        assert "CREATE TRIGGER" not in body.upper()


@pytest.mark.integration
class TestShapeAMigrationExecutes:
    """4.1 — Shape A migration is executable: upgrade creates objects, downgrade removes them."""

    def test_upgrade_and_downgrade(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        fn_ddl = [_shape_a_function(schema, t) for t in TABLES]
        trg_ddl = [_shape_a_trigger(schema, t) for t in TABLES]

        cfg = alembic_project.config
        cfg.attributes["pg_functions"] = fn_ddl
        cfg.attributes["pg_triggers"] = trg_ddl
        cfg.attributes["target_metadata"] = _reflected_metadata(alembic_project)
        revision(cfg, message="shape_a", autogenerate=True)

        upgrade(cfg, "head")
        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            trgs = inspect_triggers(conn, [schema])
            assert len(fns) == 5
            assert len(trgs) == 5

        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            trgs = inspect_triggers(conn, [schema])
            assert len(fns) == 0
            assert len(trgs) == 0


@pytest.mark.integration
class TestShapeBMigrationExecutes:
    """4.2 — Shape B migration is executable: upgrade creates objects, downgrade removes them."""

    def test_upgrade_and_downgrade(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        fn_ddl = [_shared_audit_function(schema)]
        trg_ddl = [_shape_b_trigger(schema, t) for t in TABLES]

        cfg = alembic_project.config
        cfg.attributes["pg_functions"] = fn_ddl
        cfg.attributes["pg_triggers"] = trg_ddl
        cfg.attributes["target_metadata"] = _reflected_metadata(alembic_project)
        revision(cfg, message="shape_b", autogenerate=True)

        upgrade(cfg, "head")
        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            trgs = inspect_triggers(conn, [schema])
            assert len(fns) == 1
            assert len(trgs) == 5

        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            trgs = inspect_triggers(conn, [schema])
            assert len(fns) == 0
            assert len(trgs) == 0


@pytest.mark.integration
class TestIncrementalMigrationExecutes:
    """4.3 — Incremental migration: add a trigger on top of a baseline."""

    def test_incremental_upgrade_and_downgrade(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        fn_ddl = [_shared_audit_function(schema)]
        trg_ddl = [_shape_b_trigger(schema, t) for t in TABLES]

        cfg = alembic_project.config
        cfg.attributes["pg_functions"] = fn_ddl
        cfg.attributes["pg_triggers"] = trg_ddl
        cfg.attributes["target_metadata"] = _reflected_metadata(alembic_project)

        # Baseline migration: 1 function + 5 triggers
        revision(cfg, message="baseline", autogenerate=True)
        upgrade(cfg, "head")

        # Add 6th table and trigger
        alembic_project.execute(f"CREATE TABLE {schema}.shipments (id serial PRIMARY KEY, name text)")
        trg_ddl_v2 = [_shape_b_trigger(schema, t) for t in (*TABLES, "shipments")]
        cfg2 = alembic_project.config
        cfg2.attributes["pg_functions"] = fn_ddl
        cfg2.attributes["pg_triggers"] = trg_ddl_v2
        cfg2.attributes["target_metadata"] = _reflected_metadata(alembic_project)
        revision(cfg2, message="add_shipments", autogenerate=True)

        upgrade(cfg2, "head")
        with alembic_project.connect() as conn:
            trgs = inspect_triggers(conn, [schema])
            assert len(trgs) == 6

        downgrade(cfg2, "-1")
        with alembic_project.connect() as conn:
            trgs = inspect_triggers(conn, [schema])
            assert len(trgs) == 5
            trigger_tables = {t.table_name for t in trgs}
            assert "shipments" not in trigger_tables


@pytest.mark.integration
class TestSecurityDefinerPreserved:
    """5.1 — SECURITY DEFINER round-trips through canonicalization without false diff."""

    def test_no_op_with_security_definer(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        alembic_project.execute(_shared_audit_function(schema))
        fn_ddl = [_shared_audit_function(schema)]

        content = _autogenerate(alembic_project, pg_functions=fn_ddl)
        body = _upgrade_body(content)
        assert "op.execute(" not in body


@pytest.mark.integration
class TestSecurityDefinerAdditionDetected:
    """5.2 — Adding SECURITY DEFINER is detected as a change."""

    def test_security_definer_added(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        _setup_tables(alembic_project)

        # Create function WITHOUT SECURITY DEFINER
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.audit_fn()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                INSERT INTO {schema}.audit_log (table_name, action, row_data)
                VALUES (TG_TABLE_NAME, TG_OP, row_to_json(NEW));
                RETURN NEW;
            END;
            $$
        """)

        # Desired state WITH SECURITY DEFINER
        fn_ddl = [_shared_audit_function(schema)]
        content = _autogenerate(alembic_project, pg_functions=fn_ddl)
        body = _upgrade_body(content)

        assert "op.execute(" in body
        assert "SECURITY DEFINER" in content


@pytest.mark.integration
class TestMixedOperationsOrdering:
    """6.1 — Mixed create/replace/drop operations maintain dependency order."""

    def test_dependency_ordering(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        initial_tables = ("users", "orders", "payments")
        _setup_tables(alembic_project, initial_tables)

        # Set up existing state: Shape A for 3 tables
        for t in initial_tables:
            alembic_project.execute(_shape_a_function(schema, t))
            alembic_project.execute(_shape_a_trigger(schema, t))

        # Desired state: drop 'payments' trigger, modify 'orders' function, add 2 new tables
        alembic_project.execute(f"CREATE TABLE {schema}.products (id serial PRIMARY KEY, name text)")
        alembic_project.execute(f"CREATE TABLE {schema}.inventory (id serial PRIMARY KEY, name text)")

        modified_orders_fn = f"""\
CREATE OR REPLACE FUNCTION {schema}.audit_orders()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    INSERT INTO {schema}.audit_log (table_name, action, row_data, changed_at)
    VALUES ('orders', TG_OP, row_to_json(NEW), now());
    RETURN NEW;
END;
$$"""
        kept = ("users", "orders")
        added = ("products", "inventory")
        all_tables = (*kept, *added)

        fn_ddl = [modified_orders_fn if t == "orders" else _shape_a_function(schema, t) for t in all_tables]
        trg_ddl = [_shape_a_trigger(schema, t) for t in all_tables]

        content = _autogenerate(alembic_project, pg_functions=fn_ddl, pg_triggers=trg_ddl)
        body = _upgrade_body(content)

        # Find positions of each operation type
        drop_trg_positions = [m.start() for m in re.finditer(r"DROP TRIGGER", body, re.IGNORECASE)]
        drop_fn_positions = [m.start() for m in re.finditer(r"DROP FUNCTION", body, re.IGNORECASE)]
        create_fn_positions = [m.start() for m in re.finditer(r"CREATE OR REPLACE FUNCTION", body, re.IGNORECASE)]
        create_trg_positions = [m.start() for m in re.finditer(r"CREATE TRIGGER", body, re.IGNORECASE)]

        assert drop_trg_positions, "Should have DROP TRIGGER ops"
        assert create_fn_positions, "Should have CREATE/REPLACE FUNCTION ops"
        assert create_trg_positions, "Should have CREATE TRIGGER ops"

        # Drop triggers before everything else
        if drop_fn_positions:
            assert max(drop_trg_positions) < min(drop_fn_positions)
        assert max(drop_trg_positions) < min(create_fn_positions)

        # Create/replace functions before create triggers
        assert max(create_fn_positions) < min(create_trg_positions)
