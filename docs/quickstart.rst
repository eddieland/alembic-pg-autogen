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
