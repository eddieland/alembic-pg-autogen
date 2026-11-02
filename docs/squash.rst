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

Before the first real upgrade, the squash runs a preflight. It runs your ``env.py`` with one sentinel revision and no
other revision. The sentinel fails unless ``current_database()`` is the scratch database. An ``env.py`` that connects
to another database therefore runs no migration there. Alembic stops with ``Can't locate revision`` when that database
has a version table, or the sentinel fails and its transaction rolls back. Code that ``env.py`` runs outside the
migrations, such as a ``CREATE SCHEMA``, still runs in that database.

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
``--pg-dump-arg`` (repeat it for more arguments), and ``--allow-data-loss`` (see `Rows and sequences`_).

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

1. It reads the revision map. It refuses a revision after the target whose ``down_revision`` or ``depends_on`` names a
   replaced revision other than the target. It refuses branch labels that a replaced revision other than the target
   declares.
1. It runs the preflight in its own scratch database.
1. It builds a scratch database with the old chain up to the target, and runs ``pg_dump --schema-only`` on it. The dump
   leaves out the version table.
1. It writes the new revision into a temporary copy of your script directory and version locations, without the
   replaced files.
1. It builds a second scratch database with the new revision and compares the two databases.
1. It upgrades both scratch databases to the heads and compares them again.
1. If every check passes, it writes ``<target>_squashed.py`` next to the old target file, then deletes the replaced
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

At the target, it also compares the row count of each table and the last value of each sequence.

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


    def upgrade():
        ...  # save each setting, run each statement, restore each setting


    def downgrade():
        raise NotImplementedError("3f9a1c2b7d4e squashes 212 revisions and cannot be downgraded")

``squashed_revisions`` lists every replaced ID, from base to the target. A check in your ``env.py`` or in a test can use
it to explain a database that is older than the squash.

The dump changes session settings such as ``search_path``. The revision saves each of these settings before the body
and restores it after the body, so the later revisions run with your settings. The squash removes a ``SET`` of a
setting that the server does not know. A newer ``pg_dump`` sets ``transaction_timeout``, for example, which PostgreSQL
16 and older do not know.

The body also differs from raw ``pg_dump`` output in these ways:

- ``ALTER ... OWNER TO`` the role that ran the squash is removed. The role that runs the migration owns new objects
  already. ``OWNER TO`` another role stays, so that role must exist where the revision runs, as with the old chain.
- ``COMMENT ON EXTENSION`` is removed, because only the owner of the extension can run it.
- ``CREATE SCHEMA`` of the version table schema becomes ``CREATE SCHEMA IF NOT EXISTS``.
- A ``REFRESH MATERIALIZED VIEW`` follows for each materialized view that the old chain left populated, in dependency
  order.

Limits
------

Rows and sequences
~~~~~~~~~~~~~~~~~~

A schema-only dump holds no rows. When a table has a different row count, or a sequence has a different last value, at
the target, the squash stops and lists them. Run it again with ``--allow-data-loss`` to accept the loss. The squash then
reports the differences as warnings. Add the rows to the new revision with ``op.bulk_insert()``.

Objects of the whole server
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``pg_dump`` does not dump roles, tablespaces, or ``ALTER DATABASE ... SET``. When the old chain changes one of these,
the squash stops, also when the change comes from a revision after the target. Both chains run on one server, so the
check compares the server before and after the run. Another session that changes roles during the run also stops the
squash.

At the end, the squash drops the roles and tablespaces that the run created. It cannot undo other changes, such as an
``ALTER ROLE`` of an existing role.

Other limits
~~~~~~~~~~~~

- Inherited tables can get a different column order, because ``pg_dump`` lists only the local columns of a child table.
  The column order check stops the squash in that case.
- The body uses the syntax of the server that the squash ran against. Run the squash against the oldest PostgreSQL major
  version that you support.
- The squash splits the dump with ``postgast``. A statement that ``postgast`` cannot parse, such as some PostgreSQL 18
  syntax, stops the squash.
- ``downgrade()`` of the new revision raises ``NotImplementedError``.
