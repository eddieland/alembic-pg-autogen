Migrating from alembic_utils
============================

You can pass your existing ``PGFunction`` and ``PGTrigger`` objects from
`alembic_utils <https://github.com/olirice/alembic_utils>`_ directly to this package. It accepts any object with a
``to_sql_statement_create()`` method. You can mix those objects with plain DDL strings:

.. code-block:: python

   from alembic_utils.pg_function import PGFunction

   my_func = PGFunction(schema="public", signature="my_func()", definition="...")

   PG_FUNCTIONS = [
       my_func,  # an alembic_utils object
       "CREATE FUNCTION new_func() ...",  # a plain DDL string
   ]

You can therefore migrate one declaration at a time. You do not need to rewrite every declaration at once.

``PGGrantTable``
----------------

``pg_grants`` accepts ``PGGrantTable`` objects too. A ``PGGrantTable`` with a column list writes a column privilege,
for example ``GRANT SELECT (id, name) ON public.orders TO app``. ``pg_grants`` does not compare column privileges and
rejects that statement with a ``ValueError``. Replace such an object with a table privilege string:

.. code-block:: python

   PG_GRANTS = [
       "GRANT SELECT ON public.orders TO app",
   ]
