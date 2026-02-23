Migrating from alembic_utils
============================

If you're coming from `alembic_utils <https://github.com/olirice/alembic_utils>`_, you can pass
your existing ``PGFunction`` / ``PGTrigger`` objects directly — any object with a
``to_sql_statement_create()`` method is accepted alongside plain DDL strings:

.. code-block:: python

   from alembic_utils.pg_function import PGFunction

   my_func = PGFunction(schema="public", signature="my_func()", definition="...")

   PG_FUNCTIONS = [
       my_func,  # alembic_utils object — works as-is
       "CREATE FUNCTION new_func() ...",  # plain DDL string — also works
   ]

This lets you migrate incrementally without rewriting all your declarations at once.
