"""Phase 1: YouTube OAuth connection, PKCE state, and channel metadata.

Revision ID: 0002_youtube_connection
Revises: 0001_foundation
Create Date: 2026-08-26 07:27:37.786566
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_youtube_connection"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('oauth_state',
    sa.Column('state', sa.String(length=128), nullable=False),
    sa.Column('code_verifier', sa.String(length=128), nullable=False),
    sa.Column('redirect_uri', sa.String(length=500), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('state', name=op.f('pk_oauth_state'))
    )
    op.create_index('ix_oauth_state_expires_at', 'oauth_state', ['expires_at'], unique=False)
    op.create_table('youtube_connection',
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('google_account_email', sa.String(length=320), nullable=True),
    sa.Column('access_token_enc', sa.LargeBinary(), nullable=True),
    sa.Column('refresh_token_enc', sa.LargeBinary(), nullable=True),
    sa.Column('access_token_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('token_type', sa.String(length=32), server_default='Bearer', nullable=False),
    sa.Column('scopes', sa.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='ACTIVE', nullable=False),
    sa.Column('granted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_refreshed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_error_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('audit_status', sa.String(length=20), server_default='UNAUDITED', nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channel.id'], name=op.f('fk_youtube_connection_channel_id_channel'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_youtube_connection')),
    sa.UniqueConstraint('channel_id', name=op.f('uq_youtube_connection_channel_id'))
    )
    op.create_index('ix_youtube_connection_status', 'youtube_connection', ['status'], unique=False)
    op.add_column('channel', sa.Column('thumbnail_url', sa.String(length=500), nullable=True))
    op.add_column('channel', sa.Column('uploads_playlist_id', sa.String(length=64), nullable=True))
    op.add_column('channel', sa.Column('subscriber_count', sa.BigInteger(), nullable=True))
    op.add_column('channel', sa.Column('video_count', sa.BigInteger(), nullable=True))
    op.add_column('channel', sa.Column('view_count', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('channel', 'view_count')
    op.drop_column('channel', 'video_count')
    op.drop_column('channel', 'subscriber_count')
    op.drop_column('channel', 'uploads_playlist_id')
    op.drop_column('channel', 'thumbnail_url')
    op.drop_index('ix_youtube_connection_status', table_name='youtube_connection')
    op.drop_table('youtube_connection')
    op.drop_index('ix_oauth_state_expires_at', table_name='oauth_state')
    op.drop_table('oauth_state')
