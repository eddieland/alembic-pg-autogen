"""Unit tests for the revision-directive rewriters — no database required.

The end-to-end tests in ``test_autogenerate.py`` exercise ``skip_drop_index_for_dropped_tables`` through a real
autogenerate run.  These tests pin the rewriter's contract on hand-built directive trees: which operations it removes,
which it keeps, and how it composes with other hooks.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
from typing import Any

from alembic.autogenerate.rewriter import Rewriter
from alembic.operations import ops

from alembic_pg_autogen import skip_drop_index_for_dropped_tables
from alembic_pg_autogen.rewriters import _dropped_tables, _without_drop_index

LOGGER = "alembic_pg_autogen.rewriters"

# The rewriter never touches the migration context or the revision argument, so the tests pass placeholders.
NO_CONTEXT: Any = None
NO_REVISION: Any = None


def _script(upgrade: list[ops.MigrateOperation], downgrade: list[ops.MigrateOperation] | None = None) -> Any:
    """Build a ``MigrationScript`` around the given upgrade and downgrade operations."""
    return ops.MigrationScript(None, ops.UpgradeOps(ops=upgrade), ops.DowngradeOps(ops=downgrade or []))


def _names(directives: list[ops.MigrateOperation]) -> list[str]:
    """Flatten a directive list to ``TypeName`` / ``TypeName:index_name`` labels for concise assertions."""
    labels: list[str] = []
    for op in directives:
        if isinstance(op, ops.ModifyTableOps):
            labels.append(f"ModifyTableOps({op.table_name})[{', '.join(_names(op.ops))}]")
        elif isinstance(op, ops.DropIndexOp):
            labels.append(f"DropIndexOp:{op.index_name}")
        else:
            labels.append(type(op).__name__)
    return labels


class TestRemovesRedundantDropIndex:
    """A ``drop_index`` whose table the same function drops is removed."""

    def test_drop_index_inside_modify_table_ops_is_removed(self):
        """Autogenerate nests the ``DropIndexOp`` in a ``ModifyTableOps`` just before the ``DropTableOp``."""
        script = _script([
            ops.ModifyTableOps("orders", [ops.DropIndexOp("ix_orders_customer", table_name="orders")]),
            ops.DropTableOp("orders"),
        ])

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert _names(script.upgrade_ops.ops) == ["DropTableOp"]

    def test_top_level_drop_index_is_removed(self):
        """A hand-built or rewritten ``DropIndexOp`` at the top level is removed as well."""
        script = _script([
            ops.DropIndexOp("ix_orders_customer", table_name="orders"),
            ops.DropTableOp("orders"),
        ])

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert _names(script.upgrade_ops.ops) == ["DropTableOp"]

    def test_order_of_drop_table_does_not_matter(self):
        """The table set is collected over the whole function before anything is removed."""
        script = _script([
            ops.DropTableOp("orders"),
            ops.DropIndexOp("ix_orders_customer", table_name="orders"),
        ])

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert _names(script.upgrade_ops.ops) == ["DropTableOp"]

    def test_downgrade_is_rewritten_too(self):
        """A table created in ``upgrade()`` is dropped in ``downgrade()``, with the same redundant ``drop_index``."""
        upgrade = ops.UpgradeOps(
            ops=[
                ops.CreateTableOp("orders", []),
                ops.ModifyTableOps("orders", [ops.CreateIndexOp("ix_orders_customer", "orders", ["customer_id"])]),
            ]
        )
        script: Any = ops.MigrationScript(None, upgrade, upgrade.reverse())
        assert _names(script.downgrade_ops.ops) == [
            "ModifyTableOps(orders)[DropIndexOp:ix_orders_customer]",
            "DropTableOp",
        ]

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert _names(script.upgrade_ops.ops) == [
            "CreateTableOp",
            "ModifyTableOps(orders)[CreateIndexOp]",
        ]
        assert _names(script.downgrade_ops.ops) == ["DropTableOp"]

    def test_every_upgrade_ops_container_is_rewritten(self):
        """The multi-database template carries several ``UpgradeOps`` per script."""
        first = ops.UpgradeOps(ops=[ops.DropIndexOp("ix_a", table_name="a"), ops.DropTableOp("a")])
        second = ops.UpgradeOps(ops=[ops.DropIndexOp("ix_b", table_name="b"), ops.DropTableOp("b")])
        script: Any = ops.MigrationScript(None, first, first.reverse())
        script.upgrade_ops = [first, second]
        script.downgrade_ops = [first.reverse(), second.reverse()]

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert [_names(container.ops) for container in script.upgrade_ops_list] == [["DropTableOp"], ["DropTableOp"]]

    def test_removed_operation_is_logged(self, caplog: Any):
        script = _script([ops.DropIndexOp("ix_orders_customer", table_name="orders"), ops.DropTableOp("orders")])

        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert "ix_orders_customer" in caplog.text
        assert "orders" in caplog.text


class TestKeepsEverythingElse:
    """Operations that are not a redundant ``drop_index`` pass through untouched."""

    def test_drop_index_on_surviving_table_is_kept(self):
        script = _script([
            ops.ModifyTableOps("orders", [ops.DropIndexOp("ix_orders_customer", table_name="orders")]),
            ops.DropTableOp("customers"),
        ])

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert _names(script.upgrade_ops.ops) == [
            "ModifyTableOps(orders)[DropIndexOp:ix_orders_customer]",
            "DropTableOp",
        ]

    def test_same_table_name_in_another_schema_is_kept(self):
        """Tables are matched on ``(table_name, schema)``, not on the bare name."""
        script = _script([
            ops.DropIndexOp("ix_orders_customer", table_name="orders", schema="archive"),
            ops.DropTableOp("orders"),
        ])

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert _names(script.upgrade_ops.ops) == ["DropIndexOp:ix_orders_customer", "DropTableOp"]

    def test_schema_qualified_match_is_removed(self):
        script = _script([
            ops.DropIndexOp("ix_orders_customer", table_name="orders", schema="archive"),
            ops.DropTableOp("orders", schema="archive"),
        ])

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert _names(script.upgrade_ops.ops) == ["DropTableOp"]

    def test_drop_index_without_table_name_is_kept(self):
        """A ``DropIndexOp`` that names no table cannot be matched, so it stays."""
        script = _script([ops.DropIndexOp("ix_orphan"), ops.DropTableOp("orders")])

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert _names(script.upgrade_ops.ops) == ["DropIndexOp:ix_orphan", "DropTableOp"]

    def test_container_with_other_operations_survives(self):
        """Only the redundant ``drop_index`` leaves the container; sibling operations keep it alive."""
        script = _script([
            ops.ModifyTableOps(
                "orders",
                [
                    ops.DropIndexOp("ix_orders_customer", table_name="orders"),
                    ops.DropConstraintOp("fk_orders_customer", "orders", type_="foreignkey"),
                ],
            ),
            ops.DropTableOp("orders"),
        ])

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert _names(script.upgrade_ops.ops) == ["ModifyTableOps(orders)[DropConstraintOp]", "DropTableOp"]

    def test_no_drop_table_leaves_directives_untouched(self):
        original = [
            ops.ModifyTableOps("orders", [ops.DropIndexOp("ix_orders_customer", table_name="orders")]),
            ops.CreateTableOp("customers", []),
        ]
        script = _script(list(original))

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert list(script.upgrade_ops.ops) == original

    def test_empty_script_is_a_no_op(self):
        script = _script([])

        skip_drop_index_for_dropped_tables(NO_CONTEXT, NO_REVISION, [script])

        assert list(script.upgrade_ops.ops) == []
        assert list(script.downgrade_ops.ops) == []


class TestComposition:
    """The rewriter is an Alembic ``Rewriter`` and composes through ``chain()``."""

    def test_is_a_rewriter(self):
        assert isinstance(skip_drop_index_for_dropped_tables, Rewriter)

    def test_chain_with_plain_function_runs_both(self):
        seen: list[list[str]] = []

        def record(_context: Any, _revision: Any, directives: list[Any]) -> None:
            seen.append(_names(directives[0].upgrade_ops.ops))

        chained = skip_drop_index_for_dropped_tables.chain(record)
        script = _script([ops.DropIndexOp("ix_orders_customer", table_name="orders"), ops.DropTableOp("orders")])

        chained(NO_CONTEXT, NO_REVISION, [script])

        # The plain function ran after the rewriter, so it saw the trimmed directives.
        assert seen == [["DropTableOp"]]

    def test_chain_with_another_rewriter_runs_both(self):
        other = Rewriter()

        def tag(_context: Any, _revision: Any, op: ops.DropTableOp) -> list[ops.MigrateOperation]:
            return [op, ops.ExecuteSQLOp("SELECT 1")]

        other.rewrites(ops.DropTableOp)(tag)
        chained = other.chain(skip_drop_index_for_dropped_tables)
        script = _script([ops.DropIndexOp("ix_orders_customer", table_name="orders"), ops.DropTableOp("orders")])

        chained(NO_CONTEXT, NO_REVISION, [script])

        assert _names(script.upgrade_ops.ops) == ["DropTableOp", "ExecuteSQLOp"]

    def test_chain_does_not_mutate_the_shared_rewriter(self):
        chained = skip_drop_index_for_dropped_tables.chain(lambda _context, _revision, _directives: None)

        assert chained is not skip_drop_index_for_dropped_tables
        assert skip_drop_index_for_dropped_tables._chained == ()


class TestHelpers:
    """Contracts of the private helpers the rewriter is built from."""

    def test_dropped_tables_collects_name_and_schema(self):
        directives: list[ops.MigrateOperation] = [
            ops.DropTableOp("orders"),
            ops.DropTableOp("orders", schema="archive"),
            ops.CreateTableOp("customers", []),
        ]

        assert _dropped_tables(directives) == {("orders", None), ("orders", "archive")}

    def test_without_drop_index_yields_lazily_and_filters(self):
        directives: list[ops.MigrateOperation] = [
            ops.DropIndexOp("ix_a", table_name="a"),
            ops.DropIndexOp("ix_b", table_name="b"),
        ]

        result = _without_drop_index(directives, {("a", None)})

        assert _names(list(result)) == ["DropIndexOp:ix_b"]
