Quick start
===========

Installation
------------

.. code-block:: bash

   pip install alembic-pg-autogen

Requires Python 3.10+ and SQLAlchemy 2.x.
Install a PostgreSQL driver yourself (``psycopg``, ``psycopg2``, or ``asyncpg``).

1. Declare your DDL
-------------------

Define the functions and triggers that you want to manage. Put the definitions in your ``env.py`` or in a separate
module:

.. code-block:: python

   PG_FUNCTIONS = [
       """
       CREATE OR REPLACE FUNCTION audit_trigger_func()
       RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
           NEW.updated_at = now();
           RETURN NEW;
       END;
       $$
       """,
   ]

   PG_TRIGGERS = [
       """
       CREATE TRIGGER set_updated_at
       BEFORE UPDATE ON my_table
       FOR EACH ROW EXECUTE FUNCTION audit_trigger_func()
       """,
   ]

2. Configure ``env.py``
-----------------------

Import the package to register the Alembic comparator plugin. Then pass your declarations to ``context.configure()``
inside ``run_migrations_online()``:

.. code-block:: python

   import alembic_pg_autogen  # noqa: F401  # registers the comparator plugin

   # ... in run_migrations_online():
   context.configure(
       connection=connection,
       target_metadata=target_metadata,
       autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"],
       pg_functions=PG_FUNCTIONS,
       pg_triggers=PG_TRIGGERS,
   )

.. important::

   Keep both wildcards in ``autogenerate_plugins``. The option *replaces* Alembic's default value of
   ``["alembic.autogenerate.*"]``, and it does not extend that value. A list that contains only
   ``"alembic_pg_autogen.*"`` excludes every comparator that Alembic ships. The excluded comparators include
   ``_produce_net_changes`` in ``alembic.autogenerate.schemas``, which drives the whole diff. Autogenerate then
   produces an empty migration for *every* feature, this package included, and it raises no error.

   The opposite mistake is an unset option. An import of ``alembic_pg_autogen`` registers the plugins but does not
   enable them. Alembic's default value stays in force, and no comparator from this package ever runs.

   To see what a run loaded, raise the plugin logger to ``INFO``:

   .. code-block:: python

      logging.getLogger("alembic.runtime.plugins").setLevel(logging.INFO)

   Each included plugin logs one ``setting up autogenerate plugin ...`` line. A correct configuration logs lines from
   **both** namespaces. It logs several ``alembic.autogenerate.*`` entries, among them ``schemas`` and ``tables``,
   which drive the diff. It also logs ``alembic_pg_autogen.compare`` and ``alembic_pg_autogen.checkconstraints``.
   Lines from one namespace only mean that your list is missing the other wildcard.

3. Run autogenerate as usual
----------------------------

.. code-block:: bash

   alembic revision --autogenerate -m "add audit trigger"

4. Read the generated migration
-------------------------------

The migration file contains ``op.execute()`` calls. It imports no custom operations:

.. code-block:: python

   def upgrade() -> None:
       op.execute("""CREATE OR REPLACE FUNCTION public.audit_trigger_func()
    RETURNS trigger
    LANGUAGE plpgsql
   AS $function$
       BEGIN
           NEW.updated_at = now();
           RETURN NEW;
       END;
       $function$""")
       op.execute("""CREATE TRIGGER set_updated_at BEFORE UPDATE ON public.my_table
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_func()""")


   def downgrade() -> None:
       op.execute("DROP TRIGGER set_updated_at ON public.my_table")
       op.execute("DROP FUNCTION public.audit_trigger_func()")

The DDL in ``upgrade`` is the **canonical** form that the extension reads back from PostgreSQL's catalog. The DDL is
not a copy of your input. The formatting therefore differs from your text, and the meaning stays identical.

5. What gets managed
--------------------

An object type becomes *managed* when you declare it. Inside a managed type, your declared set is the whole truth. The
extension drops each object that it finds in the inspected schemas and that you did not declare.

A key that you never pass leaves that object type unmanaged. The extension queries no catalog, compares nothing, and
emits no ``CREATE``, ``REPLACE``, or ``DROP`` operation for that type. The configuration above declares functions and
triggers, so existing views stay unchanged. You can adopt the library one object type at a time.

Pass the ``IGNORED`` sentinel to record the unmanaged object type in the configuration, or to set it conditionally. The
sentinel means exactly what an absent key means:

.. code-block:: python

   from alembic_pg_autogen import IGNORED

   context.configure(
       connection=connection,
       target_metadata=target_metadata,
       autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"],
       pg_functions=PG_FUNCTIONS,
       pg_triggers=PG_TRIGGERS,
       pg_views=IGNORED,  # views are not managed yet, so keep them
   )

An empty sequence means something else. ``pg_views=[]`` declares that the schema contains no views, so the extension
drops every existing view. ``pg_views=IGNORED`` declares that this configuration does not manage views. Note the
difference when you build the DDL list at runtime:

.. code-block:: python

   pg_views = collect_view_ddl() or IGNORED

The comparator does nothing when you declare no object type. An absent key is what leaves a type unmanaged, so a
misspelled key manages nothing. The extension logs a warning for each unrecognized ``pg_*`` option that resembles a
recognized one. The warning names the option you meant.

6. Check constraints
--------------------

Check constraints need no separate declaration. They stay in your SQLAlchemy metadata, where Alembic already manages
them.

.. code-block:: python

   class Order(Base):
       __tablename__ = "orders"

       id: Mapped[int] = mapped_column(primary_key=True)
       amount: Mapped[Decimal]

       __table_args__ = (CheckConstraint("amount > 0", name="ck_orders_amount"),)

Alembic detects a named constraint that appears or disappears. Alembic cannot decide whether two constraints with the
same name still mean the same thing. No backend-agnostic way exists to normalize SQL expressions. This package
asks PostgreSQL instead, so an edited expression produces a migration rather than silent drift:

.. code-block:: python

   def upgrade() -> None:
       op.drop_constraint("ck_orders_amount", "orders", type_="check")
       op.create_check_constraint("ck_orders_amount", "orders", "amount > 0")

This package augments Alembic
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Check constraints are the one area where this package and Alembic both run a comparator. The division of work
therefore deserves a precise description. This package fills a gap in Alembic's coverage. This package does not
replace Alembic for check constraints. Keep ``alembic.autogenerate.checkconstraint_byname`` enabled. Alembic 1.19.2
renamed that plugin to ``alembic.ext.checkconstraint_byname`` and no longer enables it through the
``alembic.autogenerate.*`` wildcard, so list it explicitly in ``autogenerate_plugins``.

.. list-table::
   :header-rows: 1
   :widths: 40 35 25

   * - Situation
     - Handled by
     - Emits
   * - Name in your models only
     - ``alembic.autogenerate.checkconstraint_byname``
     - ``create_check_constraint``
   * - Name in the database only
     - ``alembic.autogenerate.checkconstraint_byname``
     - ``drop_constraint``
   * - Name on **both** sides, expression possibly changed
     - ``alembic_pg_autogen.checkconstraints``
     - ``drop_constraint`` and ``create_check_constraint``
   * - Name on **both** sides, set of allowed values changed
     - ``alembic_pg_autogen.checkconstraints``
     - ``drop_constraint`` and ``create_check_constraint(..., postgresql_not_valid=True)``, then
       ``VALIDATE CONSTRAINT`` on the next run
   * - Name on **both** sides, database holds ``NOT VALID``
     - ``alembic_pg_autogen.checkconstraints``
     - ``ALTER TABLE ... VALIDATE CONSTRAINT``

Alembic's comparator matches constraints by name. For a name that exists on both sides, it always reports the two
constraints as equal: ``DefaultImpl.compare_check_constraint`` returns ``Equal()``, and the PostgreSQL dialect does not
override that method. This package claims that undecided case, and it claims nothing else. It ignores each name that
exists on one side alone.

The two sets of names are disjoint, so no operation is ever emitted twice. You gain nothing when you disable Alembic's
comparator, and you lose a lot. Alembic then detects no added constraint and no removed constraint, and this package
replaces none of that work.

One consequence matters in practice. Your schema may drift only through constraints that you forgot to declare and
constraints that you no longer want. This package then reports **nothing at all**, and Alembic emits every operation in
the migration. That outcome is the intended division of work rather than a failure.

How the comparison works
~~~~~~~~~~~~~~~~~~~~~~~~

The comparison sends each expression through the database. It adds the constraint to its table under a throwaway name
inside a savepoint, and then reverts the savepoint. It compares PostgreSQL's own deparsed form against the catalog.
Your model may contain ``amount >= 0``, and the catalog may contain ``amount >= 0::numeric``. The comparator reports no
difference for that pair. It does report a difference for ``amount > 0``.

Four details are worth knowing:

- The comparator only compares **named** constraints on tables that exist in ``target_metadata``. It cannot match an
  unnamed constraint by name. It compares the constraint that ``Enum(native_enum=False)`` generates, because
  PostgreSQL holds that constraint. It skips the constraint that ``Boolean(create_constraint=True)`` generates, because
  PostgreSQL has a native boolean type and SQLAlchemy never creates it there.
- The comparator adds each probe constraint as ``NOT VALID``. PostgreSQL therefore runs no table scan. Existing rows
  that violate a newly tightened constraint do not turn autogenerate into an error.
- Each added constraint takes a brief ``ACCESS EXCLUSIVE`` lock. PostgreSQL holds the lock until the autogenerate
  transaction ends. This lock is the usual reason to run ``alembic revision --autogenerate`` against a development
  database instead of a production database.
- The comparator reports a constraint that it cannot compile or apply as unchanged, and it logs a warning. It never
  raises an error for such a constraint.

Non-native enums and value sets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``Enum(Status, native_enum=False, create_constraint=True, name="ck_orders_status")`` stores the column as ``varchar``
and derives a named ``CHECK`` constraint from the Python enum. This package compares that constraint. Alembic's plugin
skips every constraint that a type generates, and it therefore reports the database copy as removed. This package runs
after that plugin, discards that drop, and adds the constraint when the database lacks it. A changed set of members
produces a migration here and nowhere else.

An added member is one migration with one validating ``create_check_constraint``. No existing row can violate a wider
set.

A removed member spans two revisions, because rows that hold the removed value need a backfill first:

1. The first revision drops the old constraint and adds the new one with ``postgresql_not_valid=True``. PostgreSQL
   enforces the new constraint for new rows at once and skips the scan of existing rows. The ``ACCESS EXCLUSIVE`` lock
   lasts about one millisecond. The migration carries a comment that names the removed values and the constraint.
2. The next ``alembic revision --autogenerate`` reads ``pg_constraint.convalidated`` and emits
   ``op.execute("ALTER TABLE orders VALIDATE CONSTRAINT ck_orders_status")``. The scan runs under
   ``SHARE UPDATE EXCLUSIVE``, which blocks no reads and no writes.

The validation revision fails with a check violation until every row holds an allowed value. Write the backfill
yourself.

The downgrade of a validation revision renders ``pass`` and a comment. PostgreSQL offers no statement that marks a
validated constraint as ``NOT VALID`` again.

Only ``col IN (...)`` and ``col = ANY (ARRAY[...])`` classify as value sets. Every other changed expression keeps the
single validating statement. Two settings change this behavior:

- ``pg_check_constraint_validation="immediate"`` in ``context.configure()`` keeps one validating statement for every
  change. The default is ``"deferred"``. Any other value raises ``ValueError``.
- ``postgresql_not_valid=True`` on a ``CheckConstraint`` in your models keeps that constraint ``NOT VALID``. The
  comparator then emits no validation for it, and the rendered ``create_check_constraint`` call carries the flag.

This comparison is a separate Alembic plugin. You can disable it and keep support for functions, triggers, and views:

.. code-block:: python

   context.configure(
       connection=connection,
       target_metadata=target_metadata,
       autogenerate_plugins=[
           "alembic.autogenerate.*",
           "alembic_pg_autogen.*",
           "~alembic_pg_autogen.checkconstraints",
       ],
   )

The exclusion above applies to *this* package's plugin. Keep ``alembic.autogenerate.checkconstraint_byname`` enabled in
both cases. An exclusion of Alembic's plugin stops the detection of added constraints and removed constraints. No
duplicate operation exists to avoid.

Requires Alembic 1.19 or newer. That release added check constraints to default autogenerate.

7. Skip ``drop_index`` for dropped tables
-----------------------------------------

Alembic emits ``op.drop_index()`` for each index of a table that the same migration drops. The `Alembic cookbook
<https://alembic.sqlalchemy.org/en/latest/cookbook.html#don-t-emit-drop-index-when-the-table-is-to-be-dropped-as-well>`_
hook that removes those calls ships here as an opt-in ``Rewriter``:

.. code-block:: python

   from alembic_pg_autogen import skip_drop_index_for_dropped_tables

   context.configure(
       ...,
       process_revision_directives=skip_drop_index_for_dropped_tables,
   )

Use ``skip_drop_index_for_dropped_tables.chain(my_hook)`` if you already pass a hook.

8. Index definitions
--------------------

You do not declare indexes to this package. Indexes stay in your SQLAlchemy metadata, and Alembic decides if an index
exists. This package adds a comparison of the index definition.

Alembic compares the columns, the expressions, uniqueness, and ``NULLS NOT DISTINCT`` of an index. Alembic does not
compare these parts:

- the ``WHERE`` predicate of a partial index
- the access method (``USING gin``, ``USING gist``, ``USING brin``)
- the ``INCLUDE`` columns
- the operator classes

If you change one of these parts in a model, Alembic emits no operation. Alembic emits no operation on later runs too.
For example, this model adds a predicate to an existing index:

.. code-block:: python

   class User(Base):
       __tablename__ = "users"

       id: Mapped[int] = mapped_column(primary_key=True)
       email: Mapped[str]
       deleted_at: Mapped[datetime | None]

       __table_args__ = (
           Index("ix_users_email", "email", postgresql_where=text("deleted_at IS NULL")),
       )

With this package, autogenerate emits this migration:

.. code-block:: python

   def upgrade() -> None:
       op.drop_index(op.f("ix_users_email"), table_name="users")
       op.create_index(
           "ix_users_email",
           "users",
           ["email"],
           unique=False,
           postgresql_where=sa.text("deleted_at IS NULL"),
       )

How the comparison works
~~~~~~~~~~~~~~~~~~~~~~~~

The comparator creates each desired index in the database and reads it back. It first makes an empty ``TEMP`` copy of
the table inside a savepoint. It creates the index on that copy. Then it compares the ``pg_get_indexdef()`` output for
the copy with the output for the real index. The savepoint rollback removes the copy.

PostgreSQL writes both definitions in one canonical form. Thus ``WHERE status IN ('a', 'b')`` in your model and
``WHERE (status = ANY (ARRAY['a'::text, 'b'::text]))`` in the catalog compare as equal. A changed predicate compares as
different.

The comparator uses a copy because ``CREATE INDEX`` builds the index. On a large table, a build takes seconds and holds
a lock. On an empty copy, a build takes less than one millisecond. The comparator never changes your table.

These rules apply:

- The comparator compares only **named** indexes on tables in ``target_metadata``.
- The comparator skips indexes that implement a primary key, unique, or exclusion constraint. The constraint owns such
  an index.
- The comparator runs *after* the Alembic comparator. It skips each index that already has an Alembic operation. Thus
  one index never gets two ``drop_index`` and ``create_index`` pairs.
- If PostgreSQL cannot create an index on the copy, the comparator logs a warning. It reports that index as unchanged.
  The comparator still compares the other indexes on the table.

Build indexes concurrently
~~~~~~~~~~~~~~~~~~~~~~~~~~

``CREATE INDEX`` takes a lock that blocks writes to the table. ``CREATE INDEX CONCURRENTLY`` does not block writes.
PostgreSQL cannot run ``CONCURRENTLY`` inside a transaction, and Alembic runs each migration in a transaction. Set
``pg_index_concurrently`` to get a migration that runs the index operations outside the transaction:

.. code-block:: python

   context.configure(
       connection=connection,
       target_metadata=target_metadata,
       autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"],
       pg_index_concurrently=True,
   )

.. code-block:: python

   def upgrade() -> None:
       with op.get_context().autocommit_block():
           op.create_index(
               "ix_users_email",
               "users",
               ["email"],
               unique=False,
               postgresql_where=sa.text("deleted_at IS NULL"),
               postgresql_concurrently=True,
           )

This option changes only the rendered migration. Autogenerate always creates an ordinary index on the copy. A
concurrent build can fail and leave an invalid index. This behavior comes from PostgreSQL, not from this package.

Index comparison is a separate Alembic plugin. To disable it, exclude the plugin:

.. code-block:: python

   context.configure(
       connection=connection,
       target_metadata=target_metadata,
       autogenerate_plugins=[
           "alembic.autogenerate.*",
           "alembic_pg_autogen.*",
           "~alembic_pg_autogen.indexes",
       ],
   )

Requires Alembic 1.19 or newer.
