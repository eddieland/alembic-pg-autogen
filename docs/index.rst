alembic-pg-autogen
==================

.. image:: https://img.shields.io/pypi/v/alembic-pg-autogen
   :target: https://pypi.org/project/alembic-pg-autogen/
   :alt: PyPI version

.. image:: https://img.shields.io/pypi/pyversions/alembic-pg-autogen
   :target: https://pypi.org/project/alembic-pg-autogen/
   :alt: Python versions

.. image:: https://img.shields.io/github/stars/eddieland/alembic-pg-autogen?style=flat
   :target: https://github.com/eddieland/alembic-pg-autogen
   :alt: GitHub stars

.. image:: https://img.shields.io/github/license/eddieland/alembic-pg-autogen
   :target: https://github.com/eddieland/alembic-pg-autogen/blob/main/LICENSE
   :alt: License

Alembic autogenerate extension for PostgreSQL functions and triggers.

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
