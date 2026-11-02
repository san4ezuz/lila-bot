"""add webhook_inbox and moves.idempotency_key

Revision ID: 7c3e1b5a9f20
Revises: 4a1f2c9d8e3b
Create Date: 2026-11-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7c3e1b5a9f20'
down_revision: Union[str, Sequence[str], None] = '4a1f2c9d8e3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('moves', sa.Column('idempotency_key', sa.String(length=128), nullable=True))
    op.create_index(
        'uq_moves_game_idempotency_key',
        'moves',
        ['game_id', 'idempotency_key'],
        unique=True,
        postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )
    op.create_table(
        'webhook_inbox',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('event_name', sa.String(length=100), nullable=True),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_webhook_inbox_status_created', 'webhook_inbox', ['status', 'created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_webhook_inbox_status_created', table_name='webhook_inbox')
    op.drop_table('webhook_inbox')
    op.drop_index('uq_moves_game_idempotency_key', table_name='moves')
    op.drop_column('moves', 'idempotency_key')
