"""Voice direction, re-exported for provider code.

The definitions moved to `app.core.narration` so `app.config` can read its
narration defaults from them without closing an import cycle (config is
imported by every provider, including this package). Provider modules keep
importing from here, which is where direction naturally belongs when reading
the TTS code.
"""

from __future__ import annotations

from app.core.narration import (
    SYSTEMDECODED_DIRECTION,
    SYSTEMDECODED_NARRATION,
    NarrationProfile,
    VoiceDirection,
    build_prompt,
)

__all__ = [
    "SYSTEMDECODED_DIRECTION",
    "SYSTEMDECODED_NARRATION",
    "NarrationProfile",
    "VoiceDirection",
    "build_prompt",
]
