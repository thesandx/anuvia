import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

import app.apps.ai_chat.models
import app.apps.payments.models
import app.apps.playroom.models
import app.models.user
from alembic import context
from app.core.config import settings
from app.core.database import Base

# Alembic can only autogenerate against models that are imported by the time
# `target_metadata` is read. The imports above exist for that side effect and
# nothing else, which is why a new app with tables has to add a line here or
# `--autogenerate` produces an empty migration. See CLAUDE.md, trap 2.
#
# Naming them here is not decoration. An import whose only purpose is a side
# effect looks unused to every static analyser — ruff wanted a `# noqa: F401`
# and CodeQL raised `py/unused-import` — and silencing that on each line
# teaches the next reader that the line is disposable. It is the opposite:
# deleting one loses a table.
REGISTERED_MODELS = (
    app.models.user,
    app.apps.ai_chat.models,
    app.apps.payments.models,
    app.apps.playroom.models,
)

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
