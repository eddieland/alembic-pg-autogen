Squashing a migration chain
===========================

A test that needs a database at head replays every revision in the chain. With hundreds of revisions, this replay
controls the time to build a test database. The ``alembic_pg_autogen.squash`` module replaces a revision that you
choose, and every ancestor of it, with one revision that builds the same schema.

The module needs no comparator from this package. A project that does not use autogenerate can use it too.

Requirements
------------

- A ``pg_dump`` binary. Its major version must be equal to or newer than the server's major version.
- A PostgreSQL server and a role that can create databases. The squash creates scratch databases from ``template0`` and
  drops them at the end. Use a server that only you use during the run (see `Objects of the whole server`_).
- An ``env.py`` that connects to the database the squash gives it. The squash sets the ``sqlalchemy.url`` option, and it
  puts an open connection without a transaction into ``config.attributes["connection"]``. Your ``env.py`` must use one
  of the two.

The generic ``env.py`` template reads ``sqlalchemy.url``. The Alembic cookbook recipe "Sharing a Connection across one
or more programmatic migration commands" reads the connection:

.. code-block:: python

    connection = config.attributes.get("connection")
    if connection is None:
        ...  # create an engine from your own settings, as before
    else:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

The connection has no open transaction. ``autocommit_block()`` and ``transaction_per_migration`` work as usual.

Before the first upgrade of your chain, the squash runs a preflight. It runs your ``env.py`` with one sentinel revision
and no other revision. The sentinel creates a marker table only when ``current_database()`` is the scratch database.
The squash stops unless that marker exists afterward, also when ``env.py`` catches the sentinel's error.

The preflight protects the revisions of your chain, not everything that ``env.py`` does. When ``env.py`` connects to
another database, these changes can still happen there, also in a dry run:

- Code in ``env.py`` that runs before ``run_migrations()``, such as a committed ``CREATE SCHEMA``.
- An empty Alembic version table, when that database has none. Alembic creates it before it runs the sentinel. With an
  ``AUTOCOMMIT`` engine, or an ``env.py`` that catches the sentinel's error, the table stays.

Read your ``env.py`` before the first run.

Choose the target
-----------------

Choose the newest revision that every deployed database has reached. Only you know this revision.

The new revision keeps the ID of the target. A database at the target or after it needs no ``stamp``. A database
before the target cannot upgrade after the squash, because Alembic cannot find its revision. Upgrade such a database to
the target with an older release first.

Run the squash
--------------

.. code-block:: text

    python -m alembic_pg_autogen.squash --config alembic.ini --target 3f9a1c2b7d4e \
        --server-url postgresql+psycopg://postgres@localhost/postgres

``--config`` works as for the ``alembic`` command. Without it, the squash reads ``alembic.ini`` and
``pyproject.toml`` in the current directory.

Add ``--dry-run`` to run every check and print the new revision without a change to a file. The other options are
``--name`` (the section of ``alembic.ini``), ``--version-table``, ``--version-table-schema``, ``--pg-dump``,
``--pg-dump-arg`` (repeat it for more arguments), ``--extra-sql``, and ``--allow-data-loss`` (see
`Rows and sequences`_).

From Python:

.. code-block:: python

    from alembic.config import Config

    from alembic_pg_autogen.squash import squash

    result = squash(
        Config("alembic.ini"),
        "3f9a1c2b7d4e",
        server_url="postgresql+psycopg://postgres@localhost/postgres",
    )
    print(result.revision_path, len(result.removed_paths))

What the squash does
--------------------

#. It reads the revision map. It refuses a revision after the target whose ``down_revision`` or ``depends_on`` names a
   replaced revision other than the target. It refuses branch labels that a replaced revision other than the target
   declares.
#. It refuses the chain when a string in a replaced revision changes an object of the whole server (see
   `Objects of the whole server`_), when a later revision imports the module of a replaced revision, or when
   ``<target>_squashed.py`` exists already.
#. It runs the preflight in its own scratch database.
#. It builds a scratch database with the old chain up to the target, and runs ``pg_dump --schema-only`` on it. The dump
   leaves out the version table.
#. It writes the new revision into a temporary copy of your script directory and version locations, without the
   replaced files.
#. It builds a second scratch database with the new revision and compares the two databases.
#. It upgrades both scratch databases to the heads and compares them again.
#. If every check passes, it writes ``<target>_squashed.py`` next to the old target file, then deletes the replaced
   files. It writes the new file to a temporary name first and renames it, so a failed write changes no file.

Commit the result as one commit. A revert of that commit restores the old chain. No database needs a change.

What the comparison checks
--------------------------

Equal ``pg_dump`` output does not prove that two databases behave the same. The squash compares these properties, at
the target and at the heads:

- the ``pg_dump --schema-only`` text, with owners
- the column order of each table, view, and materialized view
- whether each materialized view is populated
- the rows of the Alembic version table
- roles, role memberships, tablespaces, and database and role settings on the server

At the target, it also compares the row count of each table, and ``last_value`` and ``is_called`` of each sequence.

The comparison does not check row contents, statistics, or anything that a query of the system catalogs above does not
show.

The new revision
----------------

The new revision is a plain Alembic revision. It imports nothing from this package:

.. code-block:: python

    revision = "3f9a1c2b7d4e"
    down_revision = None
    branch_labels = None
    depends_on = None
    squashed_revisions = ("1a2b3c4d5e6f", ..., "3f9a1c2b7d4e")

    SETTINGS = ("statement_timeout", "search_path", "check_function_bodies", ...)
    STATEMENTS = (...)
    EXTRA_STATEMENTS = (...)
    POST_STATEMENTS = (...)


    def upgrade():
        ...  # save each setting, run STATEMENTS, restore each setting
        ...  # then run EXTRA_STATEMENTS and POST_STATEMENTS


    def downgrade():
        raise NotImplementedError("3f9a1c2b7d4e squashes 212 revisions and cannot be downgraded")

``squashed_revisions`` lists every replaced ID, from base to the target. A check in your ``env.py`` or in a test can use
it to explain a database that is older than the squash.

The dump changes session settings such as ``search_path``. The revision saves each of these settings before the body
and restores it after the body, so the later revisions run with your settings. The squash removes a ``SET`` of a
setting that the server does not know. A newer ``pg_dump`` sets ``transaction_timeout``, for example, which PostgreSQL
16 and older do not know.

The body also differs from raw ``pg_dump`` output in these ways:

- ``ALTER ... OWNER TO`` the role that ran the squash is removed, because the role that runs the migration owns new
  objects already. When a replaced revision names a role in ``OWNER TO``, every ``OWNER TO`` stays. ``OWNER TO``
  another role always stays, so that role must exist where the revision runs, as with the old chain.
- ``ALTER DEFAULT PRIVILEGES FOR ROLE`` the role that ran the squash loses its ``FOR ROLE`` clause. It then applies to
  the role that runs the migration, as the replaced revision did.
- ``COMMENT ON EXTENSION`` is removed, because only the owner of the extension can run it.
- ``CREATE SCHEMA`` of the version table schema becomes ``CREATE SCHEMA IF NOT EXISTS``.

``EXTRA_STATEMENTS`` holds the SQL of ``--extra-sql`` (see `Rows and sequences`_). ``POST_STATEMENTS`` refreshes each
materialized view that the old chain left populated, in dependency order. These run after the settings are restored, so
a function with an unqualified table name works. An unpopulated materialized view that a populated one reads is
refreshed first and emptied again with ``WITH NO DATA``. The ``OWNER TO`` of a refreshed view runs after its refresh,
because the old chain populated the view as the role that created it.

Limits
------

Rows and sequences
~~~~~~~~~~~~~~~~~~

A schema-only dump holds no rows. When a table has a different row count, or a sequence has a different ``last_value``
or ``is_called``, at the target, the squash stops and lists them.

Put the rows that your chain needs into a SQL file and pass it with ``--extra-sql FILE`` (``extra_sql`` in Python):

.. code-block:: sql

    INSERT INTO kind (code) VALUES ('a'), ('b');
    SELECT setval('kind_id_seq', 2);

The new revision stores the statements in ``EXTRA_STATEMENTS`` and runs them after the schema. The comparison runs them
too, so the squash checks them like the rest of the body. A later squash that replaces this revision carries
``EXTRA_STATEMENTS`` forward. Do not edit the rows into the new file by hand, because a later squash does not keep them.

To accept the loss instead, run the squash again with ``--allow-data-loss``. The squash then reports the differences as
warnings.

Objects of the whole server
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``pg_dump`` does not dump roles, role memberships, tablespaces, or database and system settings. A difference of the
server state cannot prove what a clean cluster needs, so the squash scans the string literals of the replaced revisions.
It refuses the chain before it connects to the server when a string contains one of these statements:

- ``CREATE``, ``ALTER``, or ``DROP`` of a role, user, group, or tablespace
- ``GRANT`` of a role and ``REVOKE`` of a role, without ``ON``
- ``ALTER DATABASE`` and ``ALTER SYSTEM``

Comments and the module docstring do not count. Move these statements into the setup of your cluster.

As a second check, the squash compares the server state before and after each upgrade of the old chain. This check also
covers a revision after the target and SQL that the scan cannot read, such as dynamic SQL. A difference stops the
squash. The squash never drops a role or a tablespace, because the difference cannot tell which session made it. The
error lists what remains on the server, and you remove it. Another session that changes roles during the run also stops
the squash, so use a server that only you use.

Other limits
~~~~~~~~~~~~

- Inherited tables can get a different column order, because ``pg_dump`` lists only the local columns of a child table.
  The column order check stops the squash in that case.
- The body uses the syntax of the server that the squash ran against. Run the squash against the oldest PostgreSQL major
  version that you support.
- The squash splits the dump with ``postgast``. A statement that ``postgast`` cannot parse, such as some PostgreSQL 18
  syntax, stops the squash.
- ``downgrade()`` of the new revision raises ``NotImplementedError``.
