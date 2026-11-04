"""Bounded, request-owned enrichment; no background jobs or cross-worker RAM state."""
from __future__ import annotations

import fcntl
import hashlib
from pathlib import Path
import tempfile
from threading import BoundedSemaphore

# Leave three of each production worker's four threads available for its price
# stream, summary and other traffic. No new threads or queued tasks. The file
# fence also prevents overlapping views of one account across workers.
_slots = BoundedSemaphore(1)
MAX_ENRICHMENT_SECONDS = 120


class ObsoletePortfolio(Exception):
    pass


def admit(user_id):
    """Return an idempotent release callback, or None. Never enqueue work.

    Fixed lock shards bound disk use and reveal no account identifiers. Hash
    collisions conservatively return busy. Locks release on process death.
    This fence is for the existing single Render instance, not distributed jobs.
    """
    if not _slots.acquire(blocking=False):
        return None
    handle = None
    try:
        root = Path(tempfile.gettempdir()) / "moneybot-portfolio-enrichment"
        root.mkdir(mode=0o700, exist_ok=True)
        shard = int(hashlib.sha256(str(user_id).encode()).hexdigest(), 16) % 512
        handle = (root / f"{shard:03x}.lock").open("a")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        if handle is not None:
            handle.close()
        _slots.release()
        return None
    released = False

    def release():
        nonlocal released
        if not released:
            released = True
            handle.close()
            _slots.release()

    return release
