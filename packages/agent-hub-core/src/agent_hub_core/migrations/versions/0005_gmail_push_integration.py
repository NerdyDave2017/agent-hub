"""Gmail Pub/Sub + watch() fields on integrations.

Revision ID: 0005_gmail_push_integration
Revises: 0004_incidents
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_gmail_push_integration"
down_revision: Union[str, Sequence[str], None] = "0004_incidents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column("email_address", sa.String(length=320), nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column("last_history_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column("watch_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column(
            "watch_active",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "integrations",
        sa.Column("watch_resource_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column(
            "connection_status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
    )
    op.create_index("ix_integrations_email_address", "integrations", ["email_address"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_integrations_email_address", table_name="integrations")
    op.drop_column("integrations", "connection_status")
    op.drop_column("integrations", "watch_resource_id")
    op.drop_column("integrations", "watch_active")
    op.drop_column("integrations", "watch_expires_at")
    op.drop_column("integrations", "last_history_id")
    op.drop_column("integrations", "email_address")
