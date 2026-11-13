"""In-process invalidation signals for authenticated panel clients.

Every mutation publishes a topic; each connected WebSocket forwards it to the
browser, which drops the matching react-query caches instead of polling.
"""
import asyncio

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    # Deep enough to keep a burst of changes, shallow enough that a stalled
    # client cannot grow the server's memory.
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    _subscribers.discard(queue)


def publish(topic: str = "support") -> None:
    for queue in tuple(_subscribers):
        try:
            queue.put_nowait(topic)
        except asyncio.QueueFull:
            pass
