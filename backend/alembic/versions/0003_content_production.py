"""Phase 2: content production — projects, research, scripts, scenes, renders, publishing.

Revision ID: 0003_content_production
Revises: 0002_youtube_connection
Create Date: 2026-08-27 07:37:40.549749
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_content_production"
down_revision: str | None = "0002_youtube_connection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('research_source',
    sa.Column('url', sa.String(length=1000), nullable=True),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('publisher', sa.String(length=300), nullable=True),
    sa.Column('source_tier', sa.String(length=30), server_default='SECONDARY', nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_source'))
    )
    op.create_table('content_project',
    sa.Column('channel_id', sa.UUID(), nullable=False),
    sa.Column('topic', sa.String(length=300), nullable=False),
    sa.Column('working_title', sa.String(length=300), nullable=True),
    sa.Column('content_pillar', sa.String(length=100), nullable=True),
    sa.Column('content_format', sa.String(length=100), nullable=True),
    sa.Column('target_viewer', sa.Text(), nullable=True),
    sa.Column('curiosity_gap', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=40), server_default='IDEA', nullable=False),
    sa.Column('status_detail', sa.Text(), nullable=True),
    sa.Column('target_duration_seconds', sa.Integer(), server_default='32', nullable=False),
    sa.Column('priority', sa.Integer(), server_default='0', nullable=False),
    sa.Column('topic_key', sa.String(length=300), nullable=True),
    sa.Column('created_by', sa.String(length=20), server_default='HUMAN', nullable=False),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('current_script_id', sa.UUID(), nullable=True),
    sa.Column('current_render_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channel.id'], name=op.f('fk_content_project_channel_id_channel'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_content_project'))
    )
    op.create_index('ix_content_project_status', 'content_project', ['status', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_content_project_topic_key', 'content_project', ['topic_key'], unique=False)
    op.create_table('project_transition',
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('from_status', sa.String(length=40), nullable=True),
    sa.Column('to_status', sa.String(length=40), nullable=False),
    sa.Column('actor', sa.String(length=40), server_default='SYSTEM', nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['content_project.id'], name=op.f('fk_project_transition_project_id_content_project'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_project_transition'))
    )
    op.create_index('ix_project_transition_project', 'project_transition', ['project_id', 'created_at'], unique=False)
    op.create_table('published_video',
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('publishing_job_id', sa.UUID(), nullable=True),
    sa.Column('youtube_video_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('privacy_status', sa.String(length=20), nullable=True),
    sa.Column('reconciled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reconciliation_method', sa.String(length=40), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['content_project.id'], name=op.f('fk_published_video_project_id_content_project'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_published_video')),
    sa.UniqueConstraint('youtube_video_id', name=op.f('uq_published_video_youtube_video_id'))
    )
    op.create_index('ix_published_video_project', 'published_video', ['project_id'], unique=False)
    op.create_table('research_note',
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('source_id', sa.UUID(), nullable=True),
    sa.Column('claim', sa.Text(), nullable=False),
    sa.Column('claim_type', sa.String(length=30), server_default='FACT', nullable=False),
    sa.Column('confidence', sa.String(length=20), server_default='HIGH', nullable=False),
    sa.Column('verification_status', sa.String(length=30), server_default='UNVERIFIED', nullable=False),
    sa.Column('used_in_script', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['content_project.id'], name=op.f('fk_research_note_project_id_content_project'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['research_source.id'], name=op.f('fk_research_note_source_id_research_source'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_research_note'))
    )
    op.create_index('ix_research_note_project', 'research_note', ['project_id'], unique=False)
    op.create_table('script',
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('title_candidates', sa.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False),
    sa.Column('selected_title', sa.String(length=300), nullable=True),
    sa.Column('hook_candidates', sa.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False),
    sa.Column('selected_hook', sa.Text(), nullable=True),
    sa.Column('narration', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('hashtags', sa.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False),
    sa.Column('authoring_mode', sa.String(length=20), server_default='manual', nullable=False),
    sa.Column('word_count', sa.Integer(), nullable=True),
    sa.Column('estimated_duration_seconds', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['content_project.id'], name=op.f('fk_script_project_id_content_project'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_script')),
    sa.UniqueConstraint('project_id', 'version', name='uq_script_project_id_version')
    )
    op.create_table('scene',
    sa.Column('script_id', sa.UUID(), nullable=False),
    sa.Column('scene_number', sa.Integer(), nullable=False),
    sa.Column('narration', sa.Text(), nullable=False),
    sa.Column('on_screen_text', sa.Text(), nullable=True),
    sa.Column('visual_instruction', sa.Text(), nullable=True),
    sa.Column('template_id', sa.String(length=100), nullable=False),
    sa.Column('template_props', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('start_seconds', sa.Numeric(precision=6, scale=3), nullable=True),
    sa.Column('end_seconds', sa.Numeric(precision=6, scale=3), nullable=True),
    sa.Column('transition_in', sa.String(length=40), nullable=True),
    sa.Column('project_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['script_id'], ['script.id'], name=op.f('fk_scene_script_id_script'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scene')),
    sa.UniqueConstraint('script_id', 'scene_number', name='uq_scene_script_id_scene_number')
    )
    op.create_table('video_render',
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('script_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='PENDING', nullable=False),
    sa.Column('renderer', sa.String(length=80), server_default='ffmpeg', nullable=False),
    sa.Column('output_path', sa.String(length=1000), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('fps', sa.Integer(), nullable=True),
    sa.Column('duration_seconds', sa.Numeric(precision=8, scale=3), nullable=True),
    sa.Column('bytes', sa.Integer(), nullable=True),
    sa.Column('checksum', sa.String(length=80), nullable=True),
    sa.Column('loudness_lufs', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('peak_dbfs', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['content_project.id'], name=op.f('fk_video_render_project_id_content_project'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['script_id'], ['script.id'], name=op.f('fk_video_render_script_id_script'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_video_render'))
    )
    op.create_index('ix_video_render_project', 'video_render', ['project_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('production_asset',
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('scene_id', sa.UUID(), nullable=True),
    sa.Column('asset_type', sa.String(length=40), nullable=False),
    sa.Column('origin', sa.String(length=30), server_default='GENERATED', nullable=False),
    sa.Column('license', sa.String(length=200), server_default='internal', nullable=False),
    sa.Column('attribution_text', sa.Text(), nullable=True),
    sa.Column('source_url', sa.String(length=1000), nullable=True),
    sa.Column('file_path', sa.String(length=1000), nullable=False),
    sa.Column('mime_type', sa.String(length=100), nullable=True),
    sa.Column('bytes', sa.Integer(), nullable=True),
    sa.Column('checksum', sa.String(length=80), nullable=True),
    sa.Column('duration_seconds', sa.Numeric(precision=8, scale=3), nullable=True),
    sa.Column('provider', sa.String(length=100), nullable=True),
    sa.Column('asset_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['content_project.id'], name=op.f('fk_production_asset_project_id_content_project'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['scene_id'], ['scene.id'], name=op.f('fk_production_asset_scene_id_scene'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_production_asset'))
    )
    op.create_index('ix_production_asset_project', 'production_asset', ['project_id', 'asset_type'], unique=False)
    op.create_index('ix_production_asset_scene', 'production_asset', ['scene_id'], unique=False)
    op.create_table('publishing_job',
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('render_id', sa.UUID(), nullable=True),
    sa.Column('provider_mode', sa.String(length=30), server_default='MANUAL_HANDOFF', nullable=False),
    sa.Column('state', sa.String(length=30), server_default='PENDING', nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('tags', sa.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False),
    sa.Column('privacy_status', sa.String(length=20), server_default='public', nullable=False),
    sa.Column('publishing_notes', sa.Text(), nullable=True),
    sa.Column('contains_synthetic_media', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('resumable_session_uri', sa.String(length=1000), nullable=True),
    sa.Column('youtube_video_id', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['content_project.id'], name=op.f('fk_publishing_job_project_id_content_project'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['render_id'], ['video_render.id'], name=op.f('fk_publishing_job_render_id_video_render'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_publishing_job'))
    )
    op.create_index('ix_publishing_job_project', 'publishing_job', ['project_id'], unique=False)
    op.create_table('quality_check',
    sa.Column('render_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=True),
    sa.Column('verdict', sa.String(length=30), server_default='FAIL', nullable=False),
    sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('blocking_issues', sa.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False),
    sa.Column('warnings', sa.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['render_id'], ['video_render.id'], name=op.f('fk_quality_check_render_id_video_render'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quality_check'))
    )

    # ARCH §13.4, database layer of the never-upload-twice guard: at most one
    # publishing job per project may be in a non-terminal state. A partial
    # unique index expresses this without blocking historical retries.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_publishing_job_live_per_project
            ON publishing_job (project_id)
         WHERE state NOT IN ('DONE', 'FAILED')
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_publishing_job_idempotency_key "
        "ON publishing_job (idempotency_key)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_publishing_job_idempotency_key")
    op.execute("DROP INDEX IF EXISTS uq_publishing_job_live_per_project")
    op.drop_table('quality_check')
    op.drop_index('ix_publishing_job_project', table_name='publishing_job')
    op.drop_table('publishing_job')
    op.drop_index('ix_production_asset_scene', table_name='production_asset')
    op.drop_index('ix_production_asset_project', table_name='production_asset')
    op.drop_table('production_asset')
    op.drop_index('ix_video_render_project', table_name='video_render')
    op.drop_table('video_render')
    op.drop_table('scene')
    op.drop_table('script')
    op.drop_index('ix_research_note_project', table_name='research_note')
    op.drop_table('research_note')
    op.drop_index('ix_published_video_project', table_name='published_video')
    op.drop_table('published_video')
    op.drop_index('ix_project_transition_project', table_name='project_transition')
    op.drop_table('project_transition')
    op.drop_index('ix_content_project_topic_key', table_name='content_project')
    op.drop_index('ix_content_project_status', table_name='content_project')
    op.drop_table('content_project')
    op.drop_table('research_source')
