alembic-pg-autogen
==================

Alembic autogenerate extension for PostgreSQL functions and triggers.

`GitHub <https://github.com/eddieland/alembic-pg-autogen>`_ |
`PyPI <https://pypi.org/project/alembic-pg-autogen/>`_

This package automates the ``op.execute()`` calls that you write by hand for each new or changed PL/pgSQL function. You
declare your DDL strings, and ``alembic revision --autogenerate`` writes the ``CREATE``, ``DROP``, and
``CREATE OR REPLACE`` statements.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   quickstart
   migrating
   api

Documentation for language models
---------------------------------

This documentation also exists in plain text formats for large language models:

- `llms.txt </llms.txt>`_ (a short overview with a link to each page)
- `llms-full.txt </llms-full.txt>`_ (the complete documentation in a single file)

See `llmstxt.org <https://llmstxt.org/>`_ for a description of the ``llms.txt`` standard.

Indices and tables
------------------

* :ref:`genindex`
* :ref:`search`
