"""add users.crisis_at

Revision ID: b5d2e8f1a4c7
Revises: 7c3e1b5a9f20
Create Date: 2026-11-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5d2e8f1a4c7'
down_revision: Union[str, Sequence[str], None] = '7c3e1b5a9f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('crisis_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'crisis_at')
