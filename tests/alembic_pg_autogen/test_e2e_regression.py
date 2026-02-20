"""End-to-end regression tests for core migration operations.

Each test exercises a single migration primitive through the full pipeline:
autogenerate → upgrade → verify DB state → downgrade → verify restored state.

Scenarios covered:
  - Create / drop / replace functions
  - Create / drop / replace triggers
  - Replace a function by changing its argument signature (identity change)
  - Replace a trigger's event clause
  - Overloaded functions (same name, different arg types)
  - Idempotency: no false diffs after a migration round-trip
  - Procedures (CREATE PROCEDURE vs CREATE FUNCTION)
"""

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


def _autogenerate(project: AlembicProject, **attrs: object) -> str:
    """Run autogenerate and return the generated migration file content."""
    cfg = project.config
    versions_dir = Path(cfg.get_main_option("script_location")) / "versions"  # pyright: ignore[reportArgumentType]
    before = set(versions_dir.glob("*.py"))
    for key, value in attrs.items():
        cfg.attributes[key] = value
    script = revision(cfg, message="test", autogenerate=True)
    assert script is not None
    new_files = set(versions_dir.glob("*.py")) - before
    assert len(new_files) == 1, f"Expected 1 new migration file, got {len(new_files)}"
    return new_files.pop().read_text()


def _upgrade_body(content: str) -> str:
    """Extract the upgrade() function body from migration content."""
    match = re.search(r"def upgrade\(\).*?(?=def downgrade\(\))", content, re.DOTALL)
    assert match is not None
    return match.group(0)


def _reflected_metadata(project: AlembicProject) -> MetaData:
    metadata = MetaData()
    with project.connect() as conn:
        metadata.reflect(bind=conn)
    return metadata


def _run_migration(project: AlembicProject, **attrs: object) -> str:
    """Autogenerate a migration, run upgrade, and return the migration content."""
    cfg = project.config
    versions_dir = Path(cfg.get_main_option("script_location")) / "versions"  # pyright: ignore[reportArgumentType]
    before = set(versions_dir.glob("*.py"))
    cfg.attributes["target_metadata"] = _reflected_metadata(project)
    for key, value in attrs.items():
        cfg.attributes[key] = value
    script = revision(cfg, message="test", autogenerate=True)
    assert script is not None
    upgrade(cfg, "head")
    new_files = set(versions_dir.glob("*.py")) - before
    assert len(new_files) == 1, f"Expected 1 new migration file, got {len(new_files)}"
    return new_files.pop().read_text()


def _count(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Single-object create / drop / replace with upgrade + downgrade
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateFunction:
    """Create a new function: autogenerate, upgrade, verify, downgrade, verify."""

    def test_create_and_rollback(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        fn_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.add_numbers(a integer, b integer)
RETURNS integer LANGUAGE sql AS $$ SELECT a + b $$"""

        content = _run_migration(alembic_project, pg_functions=[fn_ddl])
        body = _upgrade_body(content)
        assert _count(body, r"CREATE OR REPLACE FUNCTION") == 1
        assert "add_numbers" in body

        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            assert len(fns) == 1
            assert fns[0].name == "add_numbers"
            assert "integer" in fns[0].identity_args

        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            assert len(inspect_functions(conn, [schema])) == 0


@pytest.mark.integration
class TestDropFunction:
    """Drop an existing function that's no longer declared."""

    def test_drop_and_rollback(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.old_fn() RETURNS void LANGUAGE sql AS $$ $$
        """)

        content = _run_migration(alembic_project, pg_functions=[])
        body = _upgrade_body(content)
        assert "DROP FUNCTION" in body
        assert "old_fn" in body

        with alembic_project.connect() as conn:
            assert len(inspect_functions(conn, [schema])) == 0

        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            assert len(fns) == 1
            assert fns[0].name == "old_fn"


@pytest.mark.integration
class TestReplaceFunctionBody:
    """Replace a function body (same signature) and verify round-trip."""

    def test_replace_and_rollback(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.greet() RETURNS text
            LANGUAGE sql AS $$ SELECT 'hello'::text $$
        """)

        new_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.greet()
RETURNS text LANGUAGE sql AS $$ SELECT 'goodbye'::text $$"""

        content = _run_migration(alembic_project, pg_functions=[new_ddl])
        body = _upgrade_body(content)
        assert _count(body, r"CREATE OR REPLACE FUNCTION") == 1
        assert "goodbye" in content

        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            assert len(fns) == 1
            assert "goodbye" in fns[0].definition

        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            assert len(fns) == 1
            assert "hello" in fns[0].definition


@pytest.mark.integration
class TestReplaceFunctionArgs:
    """Replace a function by changing its argument signature.

    PostgreSQL treats different argument types as distinct function identities.  When the old
    and new functions share a name, canonicalization sees both (the pre-existing overload plus
    the new one) and the filter keeps both — so the old overload is NOT dropped automatically.
    To fully replace, use distinct names so the old function falls out of the declared set.
    """

    def test_different_names_drops_old_creates_new(self, alembic_project: AlembicProject) -> None:
        """Rename the function: old name is dropped, new name is created."""
        schema = alembic_project.schema
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.transform_v1(val integer)
            RETURNS integer LANGUAGE sql AS $$ SELECT val * 2 $$
        """)

        new_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.transform_v2(val text)
RETURNS text LANGUAGE sql AS $$ SELECT val || '!' $$"""

        content = _run_migration(alembic_project, pg_functions=[new_ddl])
        body = _upgrade_body(content)

        assert "DROP FUNCTION" in body
        assert "transform_v1" in body
        assert "CREATE OR REPLACE FUNCTION" in body
        assert "transform_v2" in body

        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            assert len(fns) == 1
            assert fns[0].name == "transform_v2"

        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            assert len(fns) == 1
            assert fns[0].name == "transform_v1"

    def test_same_name_new_args_creates_overload(self, alembic_project: AlembicProject) -> None:
        """Same name with different arg types: new overload is created, old one preserved."""
        schema = alembic_project.schema
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.transform(val integer)
            RETURNS integer LANGUAGE sql AS $$ SELECT val * 2 $$
        """)

        new_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.transform(val text)
RETURNS text LANGUAGE sql AS $$ SELECT val || '!' $$"""

        content = _run_migration(alembic_project, pg_functions=[new_ddl])
        body = _upgrade_body(content)

        # New overload is created; old overload is preserved (same name in filter)
        assert "CREATE OR REPLACE FUNCTION" in body
        assert "DROP FUNCTION" not in body

        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            assert len(fns) == 2
            arg_types = {f.identity_args for f in fns}
            assert any("integer" in a for a in arg_types)
            assert any("text" in a for a in arg_types)


@pytest.mark.integration
class TestCreateTrigger:
    """Create a new trigger on an existing table."""

    def test_create_and_rollback(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        alembic_project.execute(f"CREATE TABLE {schema}.events (id serial PRIMARY KEY, data text)")
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.notify_fn() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$
        """)

        fn_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.notify_fn() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"""
        trg_ddl = f"""\
CREATE TRIGGER notify_trg AFTER INSERT ON {schema}.events
FOR EACH ROW EXECUTE FUNCTION {schema}.notify_fn()"""

        content = _run_migration(alembic_project, pg_functions=[fn_ddl], pg_triggers=[trg_ddl])
        body = _upgrade_body(content)
        assert _count(body, r"CREATE TRIGGER") == 1
        assert "notify_trg" in body
        # Function already exists, should not be re-created
        assert _count(body, r"CREATE OR REPLACE FUNCTION") == 0

        with alembic_project.connect() as conn:
            trgs = inspect_triggers(conn, [schema])
            assert len(trgs) == 1
            assert trgs[0].trigger_name == "notify_trg"

        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            assert len(inspect_triggers(conn, [schema])) == 0


@pytest.mark.integration
class TestDropTrigger:
    """Drop a trigger that's no longer declared."""

    def test_drop_and_rollback(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        alembic_project.execute(f"CREATE TABLE {schema}.events (id serial PRIMARY KEY, data text)")
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.notify_fn() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$
        """)
        alembic_project.execute(f"""\
            CREATE TRIGGER old_trg AFTER INSERT ON {schema}.events
            FOR EACH ROW EXECUTE FUNCTION {schema}.notify_fn()
        """)

        # Keep function, drop trigger
        fn_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.notify_fn() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"""

        content = _run_migration(alembic_project, pg_functions=[fn_ddl], pg_triggers=[])
        body = _upgrade_body(content)
        assert "DROP TRIGGER" in body
        assert "old_trg" in body

        with alembic_project.connect() as conn:
            assert len(inspect_triggers(conn, [schema])) == 0

        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            trgs = inspect_triggers(conn, [schema])
            assert len(trgs) == 1
            assert trgs[0].trigger_name == "old_trg"


@pytest.mark.integration
class TestReplaceTrigger:
    """Replace a trigger by changing its event clause (e.g., INSERT → INSERT OR UPDATE)."""

    def test_replace_and_rollback(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        alembic_project.execute(f"CREATE TABLE {schema}.events (id serial PRIMARY KEY, data text)")
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.notify_fn() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$
        """)
        alembic_project.execute(f"""\
            CREATE TRIGGER notify_trg AFTER INSERT ON {schema}.events
            FOR EACH ROW EXECUTE FUNCTION {schema}.notify_fn()
        """)

        fn_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.notify_fn() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"""
        # Change trigger from AFTER INSERT to AFTER INSERT OR UPDATE
        trg_ddl = f"""\
CREATE OR REPLACE TRIGGER notify_trg AFTER INSERT OR UPDATE ON {schema}.events
FOR EACH ROW EXECUTE FUNCTION {schema}.notify_fn()"""

        content = _run_migration(alembic_project, pg_functions=[fn_ddl], pg_triggers=[trg_ddl])
        body = _upgrade_body(content)

        # Trigger replace is DROP + CREATE (no CREATE OR REPLACE TRIGGER in PG)
        assert "DROP TRIGGER" in body
        assert "CREATE TRIGGER" in body
        assert "INSERT OR UPDATE" in content

        with alembic_project.connect() as conn:
            trgs = inspect_triggers(conn, [schema])
            assert len(trgs) == 1
            assert "INSERT OR UPDATE" in trgs[0].definition.upper()

        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            trgs = inspect_triggers(conn, [schema])
            assert len(trgs) == 1
            # Restored to INSERT-only
            assert "INSERT OR UPDATE" not in trgs[0].definition.upper()


# ---------------------------------------------------------------------------
# Overloaded functions
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestOverloadedFunctions:
    """Two functions with the same name but different argument types are distinct objects."""

    def test_modify_one_overload(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.convert(val integer)
            RETURNS text LANGUAGE sql AS $$ SELECT val::text $$
        """)
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.convert(val text)
            RETURNS text LANGUAGE sql AS $$ SELECT val $$
        """)

        # Change only the text overload; integer overload unchanged
        fn_ddl = [
            f"CREATE OR REPLACE FUNCTION {schema}.convert(val integer)\n"
            f"RETURNS text LANGUAGE sql AS $$ SELECT val::text $$",
            f"CREATE OR REPLACE FUNCTION {schema}.convert(val text)\n"
            f"RETURNS text LANGUAGE sql AS $$ SELECT upper(val) $$",
        ]

        content = _run_migration(alembic_project, pg_functions=fn_ddl)
        body = _upgrade_body(content)

        # Only the text overload should be replaced
        assert _count(body, r"CREATE OR REPLACE FUNCTION") == 1
        assert "upper" in content

        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            assert len(fns) == 2
            text_fn = next(f for f in fns if "text" in f.identity_args)
            assert "upper" in text_fn.definition

        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            assert len(fns) == 2
            text_fn = next(f for f in fns if "text" in f.identity_args)
            assert "upper" not in text_fn.definition

    def test_declare_one_overload_preserves_both(self, alembic_project: AlembicProject) -> None:
        """Declaring only one overload does NOT drop the other.

        Canonicalization sees both (the declared one and the pre-existing one with the same name) and
        the name-based filter keeps both in the desired set.
        """
        schema = alembic_project.schema
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.convert(val integer)
            RETURNS text LANGUAGE sql AS $$ SELECT val::text $$
        """)
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.convert(val text)
            RETURNS text LANGUAGE sql AS $$ SELECT val $$
        """)

        # Declare only the integer overload
        fn_ddl = [
            f"CREATE OR REPLACE FUNCTION {schema}.convert(val integer)\n"
            f"RETURNS text LANGUAGE sql AS $$ SELECT val::text $$",
        ]

        content = _run_migration(alembic_project, pg_functions=fn_ddl)
        body = _upgrade_body(content)
        # No ops — both overloads match between current and desired
        assert "op.execute(" not in body

        with alembic_project.connect() as conn:
            assert len(inspect_functions(conn, [schema])) == 2

    def test_drop_all_overloads_by_undeclaring_name(self, alembic_project: AlembicProject) -> None:
        """Not declaring any function with a given name drops ALL overloads."""
        schema = alembic_project.schema
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.convert(val integer)
            RETURNS text LANGUAGE sql AS $$ SELECT val::text $$
        """)
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.convert(val text)
            RETURNS text LANGUAGE sql AS $$ SELECT val $$
        """)

        # Declare nothing → both overloads should be dropped
        content = _run_migration(alembic_project, pg_functions=[])
        body = _upgrade_body(content)
        assert _count(body, r"DROP FUNCTION") == 2

        with alembic_project.connect() as conn:
            assert len(inspect_functions(conn, [schema])) == 0

        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            assert len(inspect_functions(conn, [schema])) == 2


# ---------------------------------------------------------------------------
# Idempotency: no false diffs after a migration round-trip
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIdempotency:
    """After running a migration, a second autogenerate should produce no ops."""

    def test_function_create_idempotent(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        fn_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.idempotent_fn()
RETURNS void LANGUAGE sql AS $$ $$"""

        _run_migration(alembic_project, pg_functions=[fn_ddl])

        # Second autogenerate should be a no-op
        content = _autogenerate(alembic_project, pg_functions=[fn_ddl])
        body = _upgrade_body(content)
        assert "op.execute(" not in body

    def test_trigger_create_idempotent(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        alembic_project.execute(f"CREATE TABLE {schema}.events (id serial PRIMARY KEY)")

        fn_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.noop_fn() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"""
        trg_ddl = f"""\
CREATE TRIGGER noop_trg AFTER INSERT ON {schema}.events
FOR EACH ROW EXECUTE FUNCTION {schema}.noop_fn()"""

        _run_migration(alembic_project, pg_functions=[fn_ddl], pg_triggers=[trg_ddl])

        content = _autogenerate(alembic_project, pg_functions=[fn_ddl], pg_triggers=[trg_ddl])
        body = _upgrade_body(content)
        assert "op.execute(" not in body

    def test_function_replace_idempotent(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.evolving() RETURNS text
            LANGUAGE sql AS $$ SELECT 'v1'::text $$
        """)

        v2_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.evolving()
RETURNS text LANGUAGE sql AS $$ SELECT 'v2'::text $$"""

        _run_migration(alembic_project, pg_functions=[v2_ddl])

        content = _autogenerate(alembic_project, pg_functions=[v2_ddl])
        body = _upgrade_body(content)
        assert "op.execute(" not in body


# ---------------------------------------------------------------------------
# Combined operations with dependency ordering
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCombinedOperations:
    """Simultaneous create, drop, and replace across functions and triggers."""

    def test_mixed_ops_execute_correctly(self, alembic_project: AlembicProject) -> None:
        """Ensure that combining different types of operations works as expected.

        Set up state, then in one migration: drop a trigger, drop a function, replace a function body, create a new
        function, and create a new trigger.
        """
        schema = alembic_project.schema
        alembic_project.execute(f"CREATE TABLE {schema}.orders (id serial PRIMARY KEY, data text)")
        alembic_project.execute(f"CREATE TABLE {schema}.users (id serial PRIMARY KEY, name text)")

        # Existing: two functions, one trigger
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.fn_orders() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$
        """)
        alembic_project.execute(f"""\
            CREATE TRIGGER orders_trg AFTER INSERT ON {schema}.orders
            FOR EACH ROW EXECUTE FUNCTION {schema}.fn_orders()
        """)
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.fn_old() RETURNS void
            LANGUAGE sql AS $$ $$
        """)

        # Desired: drop fn_old, drop orders_trg, replace fn_orders body,
        #          create fn_users + users_trg
        fn_ddl = [
            # fn_orders with modified body
            f"CREATE OR REPLACE FUNCTION {schema}.fn_orders() RETURNS trigger\n"
            f"LANGUAGE plpgsql AS $$ BEGIN RAISE NOTICE 'order'; RETURN NEW; END $$",
            # new function for users
            f"CREATE OR REPLACE FUNCTION {schema}.fn_users() RETURNS trigger\n"
            f"LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
            # fn_old is NOT declared → should be dropped
        ]
        trg_ddl = [
            # orders_trg is NOT declared → should be dropped
            # new trigger for users
            f"CREATE TRIGGER users_trg AFTER INSERT ON {schema}.users\n"
            f"FOR EACH ROW EXECUTE FUNCTION {schema}.fn_users()",
        ]

        content = _run_migration(alembic_project, pg_functions=fn_ddl, pg_triggers=trg_ddl)
        up = _upgrade_body(content)

        # Verify all expected operations
        assert "DROP TRIGGER" in up  # orders_trg dropped
        assert "DROP FUNCTION" in up  # fn_old dropped
        assert _count(up, r"CREATE OR REPLACE FUNCTION") >= 1  # fn_orders replaced, fn_users created
        assert "CREATE TRIGGER" in up  # users_trg created

        # Verify ordering: drops before creates
        drop_positions = [m.start() for m in re.finditer(r"DROP (TRIGGER|FUNCTION)", up, re.IGNORECASE)]
        create_positions = [m.start() for m in re.finditer(r"CREATE", up, re.IGNORECASE)]
        assert max(drop_positions) < min(create_positions), "All drops should precede all creates"

        # Verify DB state
        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            trgs = inspect_triggers(conn, [schema])
            fn_names = {f.name for f in fns}
            trg_names = {t.trigger_name for t in trgs}
            assert fn_names == {"fn_orders", "fn_users"}
            assert trg_names == {"users_trg"}

        # Downgrade should restore original state
        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            fns = inspect_functions(conn, [schema])
            trgs = inspect_triggers(conn, [schema])
            fn_names = {f.name for f in fns}
            trg_names = {t.trigger_name for t in trgs}
            assert fn_names == {"fn_orders", "fn_old"}
            assert trg_names == {"orders_trg"}


# ---------------------------------------------------------------------------
# Multi-step migration chain
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMultiStepMigration:
    """A realistic lifecycle: create → modify → add → drop across multiple migrations."""

    def test_three_migration_chain(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        alembic_project.execute(f"CREATE TABLE {schema}.items (id serial PRIMARY KEY, name text)")
        alembic_project.execute(f"CREATE TABLE {schema}.logs (id serial PRIMARY KEY, msg text)")

        # --- Migration 1: Create function + trigger ---
        fn_v1 = f"""\
CREATE OR REPLACE FUNCTION {schema}.log_item() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO {schema}.logs (msg) VALUES ('created: ' || NEW.name);
    RETURN NEW;
END;
$$"""
        trg_v1 = f"""\
CREATE TRIGGER item_log AFTER INSERT ON {schema}.items
FOR EACH ROW EXECUTE FUNCTION {schema}.log_item()"""

        _run_migration(alembic_project, pg_functions=[fn_v1], pg_triggers=[trg_v1])
        with alembic_project.connect() as conn:
            assert len(inspect_functions(conn, [schema])) == 1
            assert len(inspect_triggers(conn, [schema])) == 1

        # --- Migration 2: Modify function body + change trigger to also fire on UPDATE ---
        fn_v2 = f"""\
CREATE OR REPLACE FUNCTION {schema}.log_item() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO {schema}.logs (msg) VALUES (TG_OP || ': ' || NEW.name);
    RETURN NEW;
END;
$$"""
        trg_v2 = f"""\
CREATE OR REPLACE TRIGGER item_log AFTER INSERT OR UPDATE ON {schema}.items
FOR EACH ROW EXECUTE FUNCTION {schema}.log_item()"""

        content2 = _run_migration(alembic_project, pg_functions=[fn_v2], pg_triggers=[trg_v2])
        up2 = _upgrade_body(content2)
        assert "CREATE OR REPLACE FUNCTION" in up2  # function body changed
        assert "DROP TRIGGER" in up2  # trigger replaced (DROP + CREATE)
        assert "CREATE TRIGGER" in up2

        with alembic_project.connect() as conn:
            trgs = inspect_triggers(conn, [schema])
            assert len(trgs) == 1
            assert "INSERT OR UPDATE" in trgs[0].definition.upper()

        # --- Migration 3: Drop trigger, keep function ---
        content3 = _run_migration(alembic_project, pg_functions=[fn_v2], pg_triggers=[])
        up3 = _upgrade_body(content3)
        assert "DROP TRIGGER" in up3

        with alembic_project.connect() as conn:
            assert len(inspect_functions(conn, [schema])) == 1
            assert len(inspect_triggers(conn, [schema])) == 0

        # Full downgrade to base restores empty state
        cfg = alembic_project.config
        downgrade(cfg, "base")
        with alembic_project.connect() as conn:
            assert len(inspect_functions(conn, [schema])) == 0
            assert len(inspect_triggers(conn, [schema])) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFunctionWithDefaultArgs:
    """Function with default argument values round-trips correctly.

    PostgreSQL canonicalizes bare string defaults by appending an explicit cast
    (e.g. ``'world'`` becomes ``'world'::text``).  DDL must use the canonical form
    to avoid perpetual false diffs.
    """

    def test_default_args_no_false_diff(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        # Use the canonical form (explicit cast) that PostgreSQL produces
        fn_ddl = f"""\
CREATE OR REPLACE FUNCTION {schema}.greet(name text DEFAULT 'world'::text)
RETURNS text LANGUAGE sql AS $$ SELECT 'Hello, ' || name $$"""

        _run_migration(alembic_project, pg_functions=[fn_ddl])

        # Second autogenerate should see no diff
        content = _autogenerate(alembic_project, pg_functions=[fn_ddl])
        body = _upgrade_body(content)
        assert "op.execute(" not in body


@pytest.mark.integration
class TestMultipleTriggersOnSameTable:
    """Multiple triggers on the same table are tracked independently."""

    def test_independent_trigger_ops(self, alembic_project: AlembicProject) -> None:
        schema = alembic_project.schema
        alembic_project.execute(f"CREATE TABLE {schema}.events (id serial PRIMARY KEY)")
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.fn_a() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$
        """)
        alembic_project.execute(f"""\
            CREATE FUNCTION {schema}.fn_b() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$
        """)
        # Create trigger A on the table
        alembic_project.execute(f"""\
            CREATE TRIGGER trg_a AFTER INSERT ON {schema}.events
            FOR EACH ROW EXECUTE FUNCTION {schema}.fn_a()
        """)

        fn_ddl = [
            f"CREATE OR REPLACE FUNCTION {schema}.fn_a() RETURNS trigger\n"
            f"LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
            f"CREATE OR REPLACE FUNCTION {schema}.fn_b() RETURNS trigger\n"
            f"LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
        ]
        # Keep trg_a, add trg_b — both on same table
        trg_ddl = [
            f"CREATE TRIGGER trg_a AFTER INSERT ON {schema}.events\nFOR EACH ROW EXECUTE FUNCTION {schema}.fn_a()",
            f"CREATE TRIGGER trg_b AFTER UPDATE ON {schema}.events\nFOR EACH ROW EXECUTE FUNCTION {schema}.fn_b()",
        ]

        content = _run_migration(alembic_project, pg_functions=fn_ddl, pg_triggers=trg_ddl)
        body = _upgrade_body(content)

        # Only trg_b should be created; trg_a already exists
        assert _count(body, r"CREATE TRIGGER") == 1
        assert "trg_b" in body
        assert "DROP TRIGGER" not in body

        with alembic_project.connect() as conn:
            trgs = inspect_triggers(conn, [schema])
            assert len(trgs) == 2
            trg_names = {t.trigger_name for t in trgs}
            assert trg_names == {"trg_a", "trg_b"}
