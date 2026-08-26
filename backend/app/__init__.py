"""SystemDecoded backend.

Platform note — the import side effect below is deliberate.

psycopg 3's async mode cannot run on Windows' default ProactorEventLoop; it
raises `InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`. Since
Python 3.8 that loop is the Windows default, so *every* async entrypoint
(uvicorn, the worker, the scheduler, pytest) needs the selector policy instead.

Setting it here means each entrypoint gets it without having to remember to,
including `uvicorn app.main:app`, which runs no code of ours before starting its
loop. It is a no-op on Linux, so the Docker path is unaffected — this exists
purely so the documented "running without Docker" flow works on Windows.
"""

from __future__ import annotations

import asyncio
import sys

__version__ = "0.1.0"


def _use_selector_event_loop_on_windows() -> None:
    if sys.platform != "win32":
        return
    policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy is None:  # pragma: no cover - non-Windows
        return
    if not isinstance(asyncio.get_event_loop_policy(), policy):
        asyncio.set_event_loop_policy(policy())


_use_selector_event_loop_on_windows()
