"""Add auth_provider and google_sub to users (Google Sign-In support).

Revision ID: 0007_google_auth
Revises: 0006_user_password_hash
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_google_auth"
down_revision: Union[str, Sequence[str], None] = "0006_user_password_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auth_provider",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'password'"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("google_sub", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint("uq_users_google_sub", "users", ["google_sub"])


def downgrade() -> None:
    op.drop_constraint("uq_users_google_sub", "users", type_="unique")
    op.drop_column("users", "google_sub")
    op.drop_column("users", "auth_provider")
