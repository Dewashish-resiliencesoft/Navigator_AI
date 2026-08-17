from __future__ import annotations
import json
import threading
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from navigator.app.runner import DemoHandle

class DemoStateStore:
    """Redis-backed store for serializable DemoHandle state across workers."""

    def __init__(
        self, 
        redis_url: str | None, 
        worker_id: str, 
        on_remote_stop: Callable[[UUID], None]
    ) -> None:
        self.redis_url = redis_url
        self.worker_id = worker_id
        if redis_url:
            import redis
            self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
            self._pubsub = self.redis.pubsub()
            
            def _listen():
                try:
                    self._pubsub.subscribe(f"demo:stop:{worker_id}")
                    for message in self._pubsub.listen():
                        if message["type"] == "message":
                            on_remote_stop(UUID(message["data"]))
                except Exception as exc:
                    print(f"[store] pubsub listener failed: {exc}", flush=True)
                    
            threading.Thread(target=_listen, daemon=True).start()
        else:
            self.redis = None
            self._in_memory: dict[str, str] = {}
            self._in_memory_owners: dict[str, str] = {}

    def _serialize(self, handle: DemoHandle) -> str:
        data = handle.public()
        for k, v in data.items():
            if isinstance(v, UUID):
                data[k] = str(v)
            elif isinstance(v, datetime):
                data[k] = v.isoformat()
        return json.dumps(data)

    def _deserialize(self, data_str: str) -> DemoHandle:
        data = json.loads(data_str)
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
            self.redis.set(f"demo:{handle.demo_id}", data_str, ex=86400)
        else:
            self._in_memory[str(handle.demo_id)] = data_str

    def get(self, demo_id: UUID) -> DemoHandle | None:
        if self.redis:
            data_str = self.redis.get(f"demo:{demo_id}")
            if not data_str:
                return None
        else:
            data_str = self._in_memory.get(str(demo_id))
            if not data_str:
                return None
        return self._deserialize(data_str)

    def list(self, product_id: str) -> list[DemoHandle]:
        if self.redis:
            all_demos = self.redis.hgetall(f"demos:product:{product_id}")
            return [self._deserialize(data_str) for data_str in all_demos.values()]
        else:
            return [
                self._deserialize(data_str)
                for data_str in self._in_memory.values()
                if self._deserialize(data_str).product_id == product_id
            ]

    def drop(self, handle: DemoHandle) -> None:
        did = str(handle.demo_id)
        if self.redis:
            self.redis.hdel(f"demos:product:{handle.product_id}", did)
            self.redis.delete(f"demo:{did}", f"demo_owner:{did}")
        else:
            self._in_memory.pop(did, None)
            self._in_memory_owners.pop(did, None)

    def set_owner(self, demo_id: UUID) -> None:
        if self.redis:
            self.redis.set(f"demo_owner:{demo_id}", self.worker_id, ex=86400)
        else:
            self._in_memory_owners[str(demo_id)] = self.worker_id

    def get_owner(self, demo_id: UUID) -> str | None:
        if self.redis:
            return self.redis.get(f"demo_owner:{demo_id}")
        return self._in_memory_owners.get(str(demo_id))

    def publish_stop(self, owner_id: str, demo_id: UUID) -> None:
        if self.redis:
            self.redis.publish(f"demo:stop:{owner_id}", str(demo_id))
