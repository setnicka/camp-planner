import logging
from logging.config import fileConfig

from flask import current_app

from alembic import context

config = context.config
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')

target_db = current_app.extensions['migrate'].db
engine = target_db.engine
config.set_main_option(
    'sqlalchemy.url', engine.url.render_as_string(hide_password=False).replace('%', '%%'))
target_metadata = target_db.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode: emit the SQL for a URL, no Engine needed."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode, on a connection from the app's engine."""

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.zzzcomputing.com/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    with engine.connect() as connection:
        # SQLite batch migrations rebuild a table by dropping and recreating it; with FK
        # enforcement on (extensions.py enables it per connection) that DROP is rejected
        # while other tables reference the table. Disable it for the migration. The pragma
        # only takes effect outside a transaction, so run it on the raw DBAPI connection
        # before Alembic opens one.
        is_sqlite = connection.dialect.name == "sqlite"
        if is_sqlite:
            connection.connection.dbapi_connection.execute("PRAGMA foreign_keys=OFF")

        try:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                **conf_args
            )

            with context.begin_transaction():
                context.run_migrations()
        finally:
            # FK enforcement is off on this connection; discard it (even if the migration
            # raised) so an in-process upgrade can't return a FK-off connection to the pool.
            if is_sqlite:
                connection.invalidate()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
