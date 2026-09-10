"""In-process fan-out for Server-Sent Events.

`GET /v1/rooms/{key}/stream` emits the full `Room` on every change, so play
feels immediate instead of arriving on the next 2-second poll. SSE is enough
because the channel is one-way: actions are ordinary POSTs. It needs no
WebSocket infrastructure, survives proxies, and reconnects on its own.

**The limit, stated plainly:** this broker is per process. A change made on
instance A does not wake a listener on instance B. That is the third option the
handover lists, a single worker fanning out in process, and it is why the
2-second poll in `hooks/useRoom.ts` stays as the fallback. A client that misses
a push is at most two seconds stale, never wrong.

To fan out across instances, replace `publish` and `subscribe` here with Redis
pub/sub, or with PostgreSQL LISTEN/NOTIFY on Neon's **direct** (unpooled)
connection string. LISTEN/NOTIFY does not work through the pooled endpoint,
which is PgBouncer in transaction mode.
"""

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Bounded so a listener that stops reading, a laptop that slept mid-round,
#: cannot grow a queue without limit. The oldest frame is dropped, and the next
#: change or the client's own poll brings the listener back to current.
QUEUE_LIMIT = 16


class RoomBroker:
    """Wakes every listener on a room when that room changes."""

    def __init__(self) -> None:
        self._listeners: dict[str, set[asyncio.Queue]] = defaultdict(set)

    @asynccontextmanager
    async def subscribe(self, room_key: str):
        """Yields a queue that receives one payload per change to `room_key`."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._listeners[room_key].add(queue)
        try:
            yield queue
        finally:
            listeners = self._listeners.get(room_key)
            if listeners is not None:
                listeners.discard(queue)
                if not listeners:
                    del self._listeners[room_key]

    def publish(self, room_key: str, payload: dict[str, Any]) -> None:
        """Pushes one change to every listener on a room.

        Deliberately synchronous and non-blocking. It is called after a
        transaction commits, and a slow consumer must never hold up the request
        that produced the change.
        """
        for queue in list(self._listeners.get(room_key, ())):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop the oldest frame and keep the newest: for a full room
                # snapshot, the latest one supersedes everything behind it.
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    logger.debug("Dropped an SSE frame for room %s", room_key)

    def listener_count(self, room_key: str) -> int:
        """How many streams are open on a room. Used by tests and by logging."""
        return len(self._listeners.get(room_key, ()))


broker = RoomBroker()
