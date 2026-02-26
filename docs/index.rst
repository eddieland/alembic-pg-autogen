alembic-pg-autogen
==================

Alembic autogenerate extension for PostgreSQL functions and triggers.

`GitHub <https://github.com/eddieland/alembic-pg-autogen>`_ |
`PyPI <https://pypi.org/project/alembic-pg-autogen/>`_

If you've been manually writing ``op.execute()`` calls every time you add or change a PL/pgSQL function,
this package automates that: declare your DDL strings and let ``alembic revision --autogenerate`` figure
out the ``CREATE``, ``DROP``, and ``CREATE OR REPLACE`` for you.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   quickstart
   migrating
   api

LLM-friendly documentation
--------------------------

This documentation is also available in plain-text formats designed for large language models:

- `llms.txt </llms.txt>`_ — concise overview with links to each page
- `llms-full.txt </llms-full.txt>`_ — complete documentation in a single file

See `llmstxt.org <https://llmstxt.org/>`_ for more about the ``llms.txt`` standard.

Indices and tables
------------------

* :ref:`genindex`
* :ref:`search`
