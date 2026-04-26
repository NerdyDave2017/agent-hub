"""Optional bcrypt password hash on users (dashboard JWT login).

Revision ID: 0006_user_password_hash
Revises: 0005_gmail_push_integration
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_user_password_hash"
down_revision: Union[str, Sequence[str], None] = "0005_gmail_push_integration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_hash")
