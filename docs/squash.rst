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
  drops them at the end.
- An ``env.py`` that connects to the database the squash gives it. The squash sets the ``sqlalchemy.url`` option, and it
  puts an open connection into ``config.attributes["connection"]``. Your ``env.py`` must use one of the two.

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

After each upgrade, the squash checks that the scratch database holds the expected revision. If ``env.py`` connected to
another database, the squash stops with an error. That other database is then already at the target revision, so read
your ``env.py`` before the first run.

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

Add ``--dry-run`` to run every check and print the new revision without a change to a file. The other options are
``--name`` (the section of ``alembic.ini``), ``--version-table``, ``--version-table-schema``, ``--pg-dump``, and
``--pg-dump-arg`` (repeat it for more arguments).

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
   replaced revision other than the target. It refuses branch labels on a replaced revision other than the target.
1. It builds a scratch database with the old chain up to the target, and runs ``pg_dump --schema-only --no-owner`` on
   it. The dump leaves out the version table.
1. It writes the new revision into a temporary copy of your script directory, without the replaced files.
1. It builds a second scratch database with the new revision and compares the two dumps.
1. It upgrades both scratch databases to the heads and compares the two dumps again.
1. If both comparisons pass, it deletes the replaced files and writes ``<target>_squashed.py`` next to the old target
   file.

Commit the result as one commit. A revert of that commit restores the old chain. No database needs a change.

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
and restores it after the body, so the later revisions run with your settings.

Limits
------

- A schema-only dump holds no rows. A replaced revision that inserted seed rows loses them. The squash reports each
  replaced file with a data statement. Add the rows to the new revision with ``op.bulk_insert()``.
- ``pg_dump`` does not dump roles, tablespaces, or ``ALTER DATABASE ... SET``. The squash reports each replaced file
  that changes one of these.
- The body uses the syntax of the server that the squash ran against. Run the squash against the oldest PostgreSQL major
  version that you support.
- ``downgrade()`` of the new revision raises ``NotImplementedError``.
