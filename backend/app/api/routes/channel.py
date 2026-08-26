"""Channel read/update.

Single-channel by design (`PHASE-1-ARCHITECTURE.md` §4.1). The row is seeded by
the initial migration, so these endpoints never create one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.session import get_db
from app.models.channel import Channel
from app.schemas.channel import ChannelOut, ChannelUpdate

router = APIRouter(prefix="/channel", tags=["channel"])


async def _get_channel(db: AsyncSession) -> Channel:
    channel = (
        await db.execute(select(Channel).order_by(Channel.created_at).limit(1))
    ).scalar_one_or_none()
    if channel is None:
        raise NotFoundError(
            "No channel row found. Run migrations: alembic upgrade head",
            code="channel_not_seeded",
        )
    return channel


@router.get("", response_model=ChannelOut, summary="Get the channel")
async def get_channel(db: Annotated[AsyncSession, Depends(get_db)]) -> ChannelOut:
    return ChannelOut.model_validate(await _get_channel(db))


@router.patch("", response_model=ChannelOut, summary="Update channel settings")
async def update_channel(
    body: ChannelUpdate, db: Annotated[AsyncSession, Depends(get_db)]
) -> ChannelOut:
    channel = await _get_channel(db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(channel, field, value)
    await db.flush()
    # `updated_at` is server-computed (onupdate=func.now()), so it is only
    # known to SQLAlchemy after a refresh. Without this, serialising it
    # triggers an implicit lazy load outside the awaited context, which raises
    # MissingGreenlet under the async driver instead of just being slow.
    await db.refresh(channel)
    return ChannelOut.model_validate(channel)
