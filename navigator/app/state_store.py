from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from navigator.app.runner import DemoHandle

class DemoStateStore:
    """Redis-backed store for serializable DemoHandle state."""

    def __init__(self, redis_url: str | None) -> None:
        self.redis_url = redis_url
        if redis_url:
            import redis
            self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
            self._pubsub = self.redis.pubsub()
            self._listener_thread = None
        else:
            self.redis = None
            self._in_memory: dict[str, str] = {}
            self._in_memory_owners: dict[str, str] = {}

    def _serialize(self, handle: DemoHandle) -> str:
        data = handle.public()
        # Convert UUIDs and datetimes to string
        for k, v in data.items():
            if isinstance(v, UUID):
                data[k] = str(v)
            elif isinstance(v, datetime):
                data[k] = v.isoformat()
        return json.dumps(data)

    def _deserialize(self, data_str: str) -> DemoHandle:
        data = json.loads(data_str)
        # Reconstruct UUIDs and datetimes
        for k in ["demo_id", "session_id"]:
            if data.get(k):
                data[k] = UUID(data[k])
        for k in ["started_at", "finished_at"]:
            if data.get(k):
                data[k] = datetime.fromisoformat(data[k])
        return DemoHandle(**data)

    def save(self, handle: DemoHandle) -> None:
        data_str = self._serialize(handle)
        if self.redis:
            self.redis.hset(f"demos:product:{handle.product_id}", str(handle.demo_id), data_str)
            self.redis.setex(f"demo:{handle.demo_id}", 86400, data_str) # 24h TTL
        else:
            self._in_memory[str(handle.demo_id)] = data_str

    def get(self, demo_id: UUID, product_id: str | None = None) -> DemoHandle | None:
        if self.redis:
            data_str = self.redis.get(f"demo:{demo_id}")
            if not data_str:
                return None
        else:
            data_str = self._in_memory.get(str(demo_id))
            if not data_str:
                return None
        
        handle = self._deserialize(data_str)
        if product_id and handle.product_id != product_id:
            return None
        return handle

    def list(self, product_id: str) -> list[DemoHandle]:
        if self.redis:
            handles = []
            all_demos = self.redis.hgetall(f"demos:product:{product_id}")
            for data_str in all_demos.values():
                handles.append(self._deserialize(data_str))
            return handles
        else:
            return [
                self._deserialize(data_str)
                for data_str in self._in_memory.values()
                if self._deserialize(data_str).product_id == product_id
            ]

    def set_owner(self, demo_id: UUID, worker_id: str) -> None:
        if self.redis:
            self.redis.setex(f"demo_owner:{demo_id}", 86400, worker_id)
        else:
            self._in_memory_owners[str(demo_id)] = worker_id

    def get_owner(self, demo_id: UUID) -> str | None:
        if self.redis:
            return self.redis.get(f"demo_owner:{demo_id}")
        return self._in_memory_owners.get(str(demo_id))

    def publish_stop(self, worker_id: str, demo_id: UUID) -> None:
        if self.redis:
            self.redis.publish(f"demo:stop:{worker_id}", str(demo_id))

    def start_listener(self, worker_id: str, on_stop: Callable[[UUID], None]) -> None:
        if not self.redis:
            return

        def _listen():
            self._pubsub.subscribe(f"demo:stop:{worker_id}")
            for message in self._pubsub.listen():
                if message["type"] == "message":
                    demo_id = UUID(message["data"])
                    on_stop(demo_id)

        self._listener_thread = threading.Thread(target=_listen, daemon=True)
        self._listener_thread.start()

