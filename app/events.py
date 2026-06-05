import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        payload = {"event": event, "data": data}
        stale: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            self._subscribers.discard(queue)

    async def stream(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            while True:
                payload = await queue.get()
                yield f"event: {payload['event']}\ndata: {json.dumps(payload['data'])}\n\n"
        finally:
            self._subscribers.discard(queue)


event_hub = EventHub()
