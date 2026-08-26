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
