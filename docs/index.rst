alembic-pg-autogen
==================

Alembic autogenerate extension for PostgreSQL functions and triggers.

If you've been manually writing ``op.execute()`` calls every time you add or change a PL/pgSQL function,
this package automates that — declare your DDL strings and let ``alembic revision --autogenerate`` figure
out the ``CREATE``, ``DROP``, and ``CREATE OR REPLACE`` for you.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   quickstart
   migrating
   api

Indices and tables
------------------

* :ref:`genindex`
* :ref:`search`
