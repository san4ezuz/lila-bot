"""add llm_calls and users.source

Revision ID: d3a9c6e2f8b1
Revises: b5d2e8f1a4c7
Create Date: 2026-11-03 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd3a9c6e2f8b1'
down_revision: Union[str, Sequence[str], None] = 'b5d2e8f1a4c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('source', sa.String(length=64), nullable=True))
    op.create_table(
        'llm_calls',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=True),
        sa.Column('game_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column('cache_read_tokens', sa.Integer(), nullable=True),
        sa.Column('cache_write_tokens', sa.Integer(), nullable=True),
        sa.Column('stop_reason', sa.String(length=30), nullable=True),
        sa.Column('is_fallback', sa.Boolean(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_llm_calls_created', 'llm_calls', ['created_at'], unique=False)
    op.create_index('ix_llm_calls_user_created', 'llm_calls', ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_llm_calls_user_created', table_name='llm_calls')
    op.drop_index('ix_llm_calls_created', table_name='llm_calls')
    op.drop_table('llm_calls')
    op.drop_column('users', 'source')
