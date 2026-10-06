"""industry and parsed metadata on knowledge entries

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_entries") as batch:
        batch.add_column(sa.Column("industry", sa.String(120)))
        batch.add_column(sa.Column("kind", sa.String(30), nullable=False, server_default="feedback"))
        batch.add_column(sa.Column("source_name", sa.String(255)))
        batch.add_column(sa.Column("structured", sa.JSON().with_variant(JSONB(), "postgresql")))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_entries") as batch:
        batch.drop_column("structured")
        batch.drop_column("source_name")
        batch.drop_column("kind")
        batch.drop_column("industry")
