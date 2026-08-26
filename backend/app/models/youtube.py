"""YouTube OAuth connection state.

Modelled separately from `Channel` (ARCH §7.2) because credentials have a
different lifecycle, a different security posture and a different failure mode
than channel metadata. A channel row survives disconnection; a connection does
not.

Token columns hold Fernet ciphertext and are never exposed by any API schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ConnectionStatus


class YouTubeConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """OAuth credentials for the connected YouTube channel.

    One per channel, enforced by a unique constraint: this is a single-channel
    system and two live connections would race each other's token refreshes.
    """

    __tablename__ = "youtube_connection"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("channel.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Identifies which Google account authorised us, so the UI can show whether
    # the right one was used. Not a credential.
    google_account_email: Mapped[str | None] = mapped_column(String(320))

    # --- credentials: Fernet ciphertext, never returned by any endpoint ---
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_type: Mapped[str] = mapped_column(String(32), server_default="Bearer", nullable=False)

    # Scopes Google actually granted, which can be narrower than what we asked
    # for — the user can untick boxes on the consent screen.
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ConnectionStatus.ACTIVE,
        server_default=ConnectionStatus.ACTIVE,
    )

    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ARCH §3.1: uploads from an unaudited project are permanently locked to
    # private. Tracked here so the publishing provider can refuse to use the
    # API path until this says APPROVED.
    audit_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="UNAUDITED"
    )

    channel = relationship("Channel", backref="youtube_connection")

    __table_args__ = (Index("ix_youtube_connection_status", "status"),)

    # --- convenience, all non-sensitive ---
    @property
    def is_active(self) -> bool:
        return self.status == ConnectionStatus.ACTIVE

    @property
    def has_refresh_token(self) -> bool:
        return self.refresh_token_enc is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Deliberately excludes every token field.
        return f"<YouTubeConnection channel={self.channel_id} status={self.status}>"


class OAuthState(Base):
    """Short-lived CSRF state and PKCE verifier for an in-flight OAuth round trip.

    Stored server-side rather than in a cookie so the callback cannot be forged
    by anything that can set cookies, and so a state value can be consumed
    exactly once. Rows are deleted on use and swept by a scheduled job.
    """

    __tablename__ = "oauth_state"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_oauth_state_expires_at", "expires_at"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OAuthState expires_at={self.expires_at}>"
