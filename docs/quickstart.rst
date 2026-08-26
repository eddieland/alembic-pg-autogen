Quick start
===========

Installation
------------

.. code-block:: bash

   pip install alembic-pg-autogen

Requires Python 3.10+ and SQLAlchemy 2.x.
Bring your own PostgreSQL driver (``psycopg``, ``psycopg2``, ``asyncpg``, etc.).

1. Declare your DDL
-------------------

In your ``env.py`` (or a separate module), define the functions and triggers you want managed:

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

2. Wire it into ``env.py``
--------------------------

Import the package (this registers the Alembic comparator plugin), then in your
``run_migrations_online()`` function pass them as keyword arguments to ``context.configure()``:

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

   Both wildcards belong in ``autogenerate_plugins``. The option *replaces* Alembic's default of
   ``["alembic.autogenerate.*"]`` rather than adding to it, so listing only ``"alembic_pg_autogen.*"`` excludes every
   comparator Alembic ships, including ``_produce_net_changes`` in ``alembic.autogenerate.schemas``, which drives the
   whole diff. Autogenerate then produces an empty migration for *everything*, this package included, without raising
   an error.

   The mirror-image mistake is leaving the option unset. Importing ``alembic_pg_autogen`` registers the plugins but
   does not opt them in, so the default stays in force and none of this package's comparators ever run.

   To see what a run actually loaded, raise the plugin logger to ``INFO``:

   .. code-block:: python

      logging.getLogger("alembic.runtime.plugins").setLevel(logging.INFO)

   Each included plugin logs one ``setting up autogenerate plugin ...`` line. A correct configuration shows lines from
   **both** namespaces: several ``alembic.autogenerate.*`` entries, among them ``schemas`` and ``tables``, which drive
   the diff, alongside ``alembic_pg_autogen.compare`` and ``alembic_pg_autogen.checkconstraints``. Lines from only one
   namespace mean the corresponding wildcard is missing from the list.

3. Autogenerate as usual
------------------------

.. code-block:: bash

   alembic revision --autogenerate -m "add audit trigger"

4. Generated migration
----------------------

The migration file will contain ``op.execute()`` calls with no custom op imports needed:

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

Note that the ``upgrade`` DDL is the **canonical** form read back from PostgreSQL's catalog,
not a copy of your input. This means formatting will differ from what you wrote, but the
semantics are identical.

5. What gets managed
--------------------

An object type becomes *managed* when you declare it, and within a managed type the declared set is the whole
truth: anything found in the inspected schemas that you did not declare is dropped.

A key you never pass leaves that object type unmanaged — its catalog is never queried, nothing is diffed, and no
``CREATE``/``REPLACE``/``DROP`` operations are emitted for it. The configuration above declares functions and
triggers, so existing views are left untouched, and you can adopt the library one object type at a time.

To record that opt-out at the configuration site, or to set it conditionally, pass the ``IGNORED`` sentinel. It
means exactly what omitting the key means:

.. code-block:: python

   from alembic_pg_autogen import IGNORED

   context.configure(
       connection=connection,
       target_metadata=target_metadata,
       autogenerate_plugins=["alembic.autogenerate.*", "alembic_pg_autogen.*"],
       pg_functions=PG_FUNCTIONS,
       pg_triggers=PG_TRIGGERS,
       pg_views=IGNORED,  # leave views alone for now
   )

Neither is the same as passing an empty sequence. ``pg_views=[]`` declares "there should be no views", so every
existing view is dropped; ``pg_views=IGNORED`` declares "views are none of my business". Watch out for this if you
build the DDL list dynamically:

.. code-block:: python

   pg_views = collect_view_ddl() or IGNORED

With no object type declared the comparator does nothing at all. Since an absent key is what leaves a type
unmanaged, a misspelled key would silently manage nothing; unrecognized ``pg_*`` options that look like a
recognized one are logged as a warning naming the key you meant.

6. Check constraints
--------------------

Check constraints need no declaration of their own: they stay in your SQLAlchemy metadata, where Alembic already
manages them.

.. code-block:: python

   class Order(Base):
       __tablename__ = "orders"

       id: Mapped[int] = mapped_column(primary_key=True)
       amount: Mapped[Decimal]

       __table_args__ = (CheckConstraint("amount > 0", name="ck_orders_amount"),)

Alembic notices when a named constraint appears or disappears, but it cannot tell whether two constraints sharing a
name still mean the same thing — normalizing SQL expressions is not possible in a backend-agnostic way. This package
asks PostgreSQL instead, so an edited expression produces a migration rather than silent drift:

.. code-block:: python

   def upgrade() -> None:
       op.drop_constraint("ck_orders_amount", "orders", type_="check")
       op.create_check_constraint("ck_orders_amount", "orders", "amount > 0")

This augments Alembic; it does not supersede it
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Check constraints are the one area where this package and Alembic both have a comparator running, so it is worth being
precise about who does what. This package fills a gap in Alembic's coverage; it does not take over check constraints.
Leave ``alembic.autogenerate.checkconstraint_byname`` enabled.

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
     - ``drop_constraint`` + re-create

Alembic's comparator matches by name and, for a name present on both sides, always reports the two as equal:
``DefaultImpl.compare_check_constraint`` returns ``Equal()`` and the PostgreSQL dialect does not override it. That
undecided case is exactly and only what this package claims; it ignores names that exist on one side alone.

Because the two sets are disjoint, no operation is ever emitted twice, so there is nothing to be gained by disabling
Alembic's comparator and a great deal to lose: added and removed constraints would stop being detected entirely, with
nothing from this package to replace them.

The practical consequence: a schema whose only drift is constraints you forgot to declare and ones you no longer want
will see **nothing at all** from this package, and every operation in the migration will have come from Alembic. That
is the intended division of labor rather than a failure.

How the comparison works
~~~~~~~~~~~~~~~~~~~~~~~~

The comparison round-trips each expression through the database: the constraint is added to its table under a
throwaway name inside a savepoint that is rolled back, and PostgreSQL's own deparsed form is compared against the
catalog. That is why ``amount >= 0`` in your model and ``amount >= 0::numeric`` in the catalog are not reported as a
difference, while ``amount > 0`` is.

A few details worth knowing:

- Only **named** constraints on tables present in ``target_metadata`` are compared. Unnamed constraints cannot be
  matched by name, and constraints generated by a type (such as ``Enum(native_enum=False)``) are left to Alembic.
- Probe constraints are added ``NOT VALID``, so no table scan happens and existing rows that would violate a newly
  tightened constraint do not turn autogenerate into an error.
- Adding a constraint takes a brief ``ACCESS EXCLUSIVE`` lock, held until the autogenerate transaction ends. This is
  the usual reason to point ``alembic revision --autogenerate`` at a development database rather than a production one.
- A constraint that cannot be compiled or applied is reported as unchanged with a warning, never as an error.

This comparison is a separate Alembic plugin, so you can turn it off while keeping function, trigger, and view
support:

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

Note that the exclusion applies to *this* package's plugin. Leave ``alembic.autogenerate.checkconstraint_byname``
enabled either way: excluding it stops added and removed constraints from being detected at all, and there is no
duplication to avoid by turning it off.

7. Indexes
----------

Indexes, like check constraints, need no declaration of their own: they stay in your SQLAlchemy metadata, where
Alembic already manages whether they exist. What this package adds is a comparison of what an index *does*.

Alembic's index signature covers columns, expressions, uniqueness, and ``NULLS NOT DISTINCT``. It does not cover the
``WHERE`` predicate of a partial index, the access method, the ``INCLUDE`` columns, or the operator classes. Change any
of those in a model and stock autogenerate emits nothing, on every run:

.. code-block:: python

   class User(Base):
       __tablename__ = "users"

       id: Mapped[int] = mapped_column(primary_key=True)
       email: Mapped[str]
       deleted_at: Mapped[datetime | None]

       __table_args__ = (
           Index("ix_users_email", "email", postgresql_where=text("deleted_at IS NULL")),
       )

With this package the added predicate produces a migration:

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

Each index is round-tripped through the database. The desired index is built inside a savepoint on an empty ``TEMP``
clone of its table, and PostgreSQL's own ``pg_get_indexdef()`` output is compared against the catalog's. That is why
``WHERE status IN ('a', 'b')`` in your model and ``WHERE (status = ANY (ARRAY['a'::text, 'b'::text]))`` in the catalog
are not reported as a difference, while a genuinely changed predicate is.

The clone matters. ``CREATE INDEX`` has no ``NOT VALID`` equivalent, so it really builds the index, and probing the real
table would cost seconds per index on a large table and hold a lock for the rest of the run. On an empty clone the
same probe costs under a millisecond, and your table is never touched.

A few details worth knowing:

- Only **named** indexes on tables present in ``target_metadata`` are compared.
- Indexes backing a primary key, unique, or exclusion constraint are skipped: the constraint owns them.
- This comparator runs *after* Alembic's and skips any index Alembic already emitted operations for, so one index
  never draws two ``drop_index`` / ``create_index`` pairs.
- An index that cannot be built is reported as unchanged with a warning, never as an error. Each probe is isolated, so
  one unusable index does not cost the rest of the table its comparison.

Building indexes concurrently
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``CREATE INDEX`` takes a lock that blocks writes. PostgreSQL's ``CONCURRENTLY`` avoids that but cannot run inside a
transaction, and Alembic runs migrations in one. Opt in with ``pg_index_concurrently`` and the generated migration
carries the block it needs:

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

This affects only what is rendered; autogenerate always probes with an ordinary ``CREATE INDEX`` on the clone. Be aware
that a concurrent build can fail and leave an invalid index behind. That is a property of ``CONCURRENTLY`` itself rather
than of this package.

Index comparison is a separate Alembic plugin, so you can turn it off on its own:

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

Requires Alembic 1.19 or newer, the release that made check constraints part of default autogenerate.
