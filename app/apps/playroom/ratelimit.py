"""Per-address rate limits.

There is no login, so nothing else stops a script. The limits come from the
handover: room creation about 10 per hour, joins about 30 per hour, actions
about 5 per second.

Two properties this deliberately has:

- **The address is never stored.** It is hashed with a per-process salt, used as
  a bucket key, and dropped when the window ends. The product promises nothing
  is kept against a player, and an IP address in a table would contradict it.
- **It is per process.** Three Cloud Run instances mean roughly three times the
  limit. That is the right trade for a party game: a shared limiter needs Redis,
  and the limit exists to stop a script, not to be exact.
"""

import hashlib
import secrets
import time
from collections import defaultdict, deque

from app.apps.playroom.errors import RateLimited

#: Rotated per process, so a bucket key cannot be reversed to an address even
#: from a memory dump, and cannot be correlated across restarts.
_SALT = secrets.token_bytes(16)


class SlidingWindowLimiter:
    """Counts hits per key in a moving window."""

    def __init__(self, limit: int, window_seconds: float, message: str) -> None:
        self.limit = limit
        self.window = window_seconds
        self.message = message
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    #: Prune once the map grows past this. Every distinct address makes an
    #: entry, and nothing else removes one until the sweeper runs, which is a
    #: scheduled job that may not be scheduled. A limiter must not be the thing
    #: that exhausts memory.
    MAX_BUCKETS = 20_000

    def check(self, key: str) -> None:
        """Records one hit, or raises `RateLimited`."""
        if len(self._hits) > self.MAX_BUCKETS:
            self.prune()

        now = time.monotonic()
        hits = self._hits[key]
        cutoff = now - self.window
        while hits and hits[0] < cutoff:
            hits.popleft()

        if len(hits) >= self.limit:
            retry_after = max(1, int(hits[0] + self.window - now) + 1)
            raise RateLimited(self.message, retry_after)

        hits.append(now)

    def reset(self) -> None:
        """Forgets every bucket. For tests, which are all one caller."""
        self._hits.clear()

    def prune(self) -> None:
        """Drops empty buckets. Called by the sweeper so memory does not creep."""
        cutoff = time.monotonic() - self.window
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]


def bucket_key(client_ip: str | None) -> str:
    """A salted hash of the caller's address. The address itself is not kept."""
    raw = (client_ip or "unknown").encode("utf-8")
    return hashlib.blake2b(raw, key=_SALT, digest_size=16).hexdigest()


create_limiter = SlidingWindowLimiter(
    limit=10,
    window_seconds=3600,
    message="Too many rooms created from here. Wait a little and try again.",
)

join_limiter = SlidingWindowLimiter(
    limit=30,
    window_seconds=3600,
    message="Too many join attempts from here. Wait a little and try again.",
)

action_limiter = SlidingWindowLimiter(
    limit=5,
    window_seconds=1,
    message="You are going too fast. Try that again in a second.",
)

ALL_LIMITERS = (create_limiter, join_limiter, action_limiter)
