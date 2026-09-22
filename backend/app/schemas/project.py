"""Request contracts for content-project actions."""

from pydantic import BaseModel, ConfigDict


class ManualPublicationConfirmation(BaseModel):
    """A user-confirmed YouTube ID; remote ownership is verified by the service."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    youtube_video_id: str
