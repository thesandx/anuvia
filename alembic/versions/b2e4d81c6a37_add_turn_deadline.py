"""add a turn deadline to playroom rounds

A turn that runs out is played automatically, so one player who closes their
tab cannot hold up everybody else. The deadline lives on the round because that
is what the turn belongs to, and because it has to survive the request that set
it. The client cannot be trusted to time its own turn, and a client that has
gone is exactly the case this exists for.

Nullable: rounds already in flight when this deploys have no deadline, and a
null deadline is simply never due. They finish under the old rules rather than
having every turn expire at once.

Revision ID: b2e4d81c6a37
Revises: a1f3c7d90b21
Create Date: 2026-09-10 08:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2e4d81c6a37"
down_revision: str | None = "a1f3c7d90b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "playroom_rounds",
        sa.Column("turn_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("playroom_rounds", "turn_expires_at")
