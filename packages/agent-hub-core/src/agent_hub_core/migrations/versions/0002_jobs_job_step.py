"""Add jobs.job_step for worker / hub progress logging.

Revision ID: 0002_job_step
Revises: 0001_initial
Create Date: 2026-04-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_job_step"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("job_step", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "job_step")
