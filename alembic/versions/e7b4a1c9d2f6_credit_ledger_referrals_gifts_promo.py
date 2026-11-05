"""credit ledger, referrals, gifts, promo codes, share cards

Moves `users.paid_games_available` into `credit_ledger`: each user with a positive counter gets
one row with reason='legacy', then the column is dropped.

Revision ID: e7b4a1c9d2f6
Revises: d3a9c6e2f8b1
Create Date: 2026-11-05 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e7b4a1c9d2f6'
down_revision: Union[str, Sequence[str], None] = 'd3a9c6e2f8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('ref_code', sa.String(length=16), nullable=True))
    op.add_column('users', sa.Column('referred_by', sa.BigInteger(), nullable=True))
    op.create_unique_constraint('users_ref_code_key', 'users', ['ref_code'])
    op.create_foreign_key('users_referred_by_fkey', 'users', 'users', ['referred_by'], ['id'])

    op.create_table(
        'credit_ledger',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('delta', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=20), nullable=False),
        sa.Column('ref', sa.String(length=128), nullable=True),
        sa.Column('game_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_credit_ledger_user', 'credit_ledger', ['user_id'], unique=False)
    op.create_index(
        'uq_credit_ledger_grant',
        'credit_ledger',
        ['user_id', 'reason', 'ref'],
        unique=True,
        postgresql_where=sa.text('delta > 0 AND ref IS NOT NULL'),
    )
    op.execute(
        "INSERT INTO credit_ledger (user_id, delta, reason) "
        "SELECT id, paid_games_available, 'legacy' FROM users WHERE paid_games_available > 0"
    )
    op.drop_column('users', 'paid_games_available')

    op.create_table(
        'gifts',
        sa.Column('code', sa.String(length=16), nullable=False),
        sa.Column('from_user_id', sa.BigInteger(), nullable=False),
        sa.Column('redeemed_by', sa.BigInteger(), nullable=True),
        sa.Column('redeemed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['from_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['redeemed_by'], ['users.id']),
        sa.PrimaryKeyConstraint('code'),
    )
    op.create_table(
        'promo_codes',
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('credits', sa.Integer(), nullable=False),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('code'),
    )
    op.create_table(
        'share_cards',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('game_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('format', sa.String(length=8), nullable=False),
        sa.Column('with_intention', sa.Boolean(), nullable=False),
        sa.Column('file_id', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['game_id'], ['games.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('game_id', 'format', 'with_intention', name='uq_share_cards_variant'),
    )
    op.create_index('ix_share_cards_user_created', 'share_cards', ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_share_cards_user_created', table_name='share_cards')
    op.drop_table('share_cards')
    op.drop_table('promo_codes')
    op.drop_table('gifts')

    op.add_column(
        'users',
        sa.Column('paid_games_available', sa.Integer(), server_default='0', nullable=False),
    )
    op.execute(
        "UPDATE users SET paid_games_available = GREATEST(balance, 0) "
        "FROM (SELECT user_id, SUM(delta) AS balance FROM credit_ledger GROUP BY user_id) AS b "
        "WHERE users.id = b.user_id"
    )
    op.drop_index('uq_credit_ledger_grant', table_name='credit_ledger')
    op.drop_index('ix_credit_ledger_user', table_name='credit_ledger')
    op.drop_table('credit_ledger')

    op.drop_constraint('users_referred_by_fkey', 'users', type_='foreignkey')
    op.drop_constraint('users_ref_code_key', 'users', type_='unique')
    op.drop_column('users', 'referred_by')
    op.drop_column('users', 'ref_code')
