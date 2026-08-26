"""UUIDv7 generation.

UUIDv7 embeds a millisecond timestamp in its high bits, so primary keys sort by
creation time. That keeps B-tree inserts local (unlike UUIDv4) and makes
"newest first" queries cheap without a secondary index on created_at.

Python's stdlib gains uuid7 in 3.14; this is a ~20-line stand-in so we do not
carry a dependency for it. Drop this module when the runtime provides it.

Ordering guarantee: strictly increasing across distinct milliseconds. Within a
single millisecond, ordering is random — deliberately not solved here, because
nothing in this system depends on sub-millisecond ordering.
"""

from __future__ import annotations

import os
import time
import uuid

__all__ = ["timestamp_ms_from_uuid7", "uuid7"]


def uuid7() -> uuid.UUID:
    """Generate a time-ordered UUID version 7."""
    ts_ms = time.time_ns() // 1_000_000
    b = bytearray(16)
    b[0:6] = ts_ms.to_bytes(6, "big")
    b[6:16] = os.urandom(10)
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(b))


def timestamp_ms_from_uuid7(value: uuid.UUID) -> int:
    """Extract the embedded millisecond timestamp. Useful for debugging."""
    return int.from_bytes(value.bytes[0:6], "big")
