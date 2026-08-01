import re

with open("navigator/app/runner.py", "r") as f:
    content = f.read()

store_code = """
import json
import time

class DemoStateStore:
    def __init__(self, redis_url: str | None, worker_id: str, on_remote_stop: Callable[[UUID], None]) -> None:
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
                    print(f"[store] pubsub listener failed: {exc}")
            threading.Thread(target=_listen, daemon=True).start()
        else:
            self.redis = None
            self._in_memory = {}
            self._in_memory_owners = {}

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
            self.redis.setex(f"demo:{handle.demo_id}", 86400, data_str)
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

    def set_owner(self, demo_id: UUID) -> None:
        if self.redis:
            self.redis.setex(f"demo_owner:{demo_id}", 86400, self.worker_id)
        else:
            self._in_memory_owners[str(demo_id)] = self.worker_id

    def get_owner(self, demo_id: UUID) -> str | None:
        if self.redis:
            return self.redis.get(f"demo_owner:{demo_id}")
        return self._in_memory_owners.get(str(demo_id))

    def publish_stop(self, owner_id: str, demo_id: UUID) -> None:
        if self.redis:
            self.redis.publish(f"demo:stop:{owner_id}", str(demo_id))

"""

content = content.replace("class _RecordingSpeaker:", store_code + "\nclass _RecordingSpeaker:")

init_replacement = """    def __init__(
        self,
        db_path: str,
        headful: bool = False,
        archive_dir: str | Path = "archives",
        redis_url: str | None = None,
    ) -> None:
        self.db_path = db_path
        self.headful = headful
        self.archive_dir = Path(archive_dir)
        self._demos: dict[UUID, DemoHandle] = {}
        self._lock = threading.Lock()
        
        self.worker_id = str(uuid4())
        self._store = DemoStateStore(redis_url, self.worker_id, self._on_remote_stop)
        threading.Thread(target=self._sync_loop, daemon=True).start()

    def _on_remote_stop(self, demo_id: UUID) -> None:
        handle = self._demos.get(demo_id)
        if handle:
            handle._stop.set()

    def _sync_loop(self) -> None:
        while True:
            with self._lock:
                handles = list(self._demos.values())
            for h in handles:
                self._store.save(h)
            time.sleep(1.0)"""

content = re.sub(r'    def __init__\([\s\S]*?    def start\(', init_replacement + '\n\n    def start(', content)

start_code_repl = """        with self._lock:
            self._demos[handle.demo_id] = handle
        self._store.save(handle)
        self._store.set_owner(handle.demo_id)"""

content = content.replace("""        with self._lock:
            self._demos[handle.demo_id] = handle""", start_code_repl)

get_code_repl = """    def get(self, demo_id: UUID, product_id: str | None = None) -> DemoHandle | None:
        \"\"\"A demo by id, scoped to a product so one tenant can't read another's.\"\"\"
        handle = self._demos.get(demo_id)
        if handle is None:
            handle = self._store.get(demo_id)
        if handle is None:
            return None
        if product_id is not None and handle.product_id != product_id:
            return None
        return handle"""
content = re.sub(r'    def get\([\s\S]*?    def list\(', get_code_repl + '\n\n    def list(', content)

list_code_repl = """    def list(self, product_id: str) -> list[DemoHandle]:
        # Merge local and remote
        remote = {h.demo_id: h for h in self._store.list(product_id)}
        local = {h.demo_id: h for h in self._demos.values() if h.product_id == product_id}
        remote.update(local)
        return list(remote.values())"""
content = re.sub(r'    def list\([\s\S]*?    def stop\(', list_code_repl + '\n\n    def stop(', content)

stop_code_repl = """    def stop(
        self,
        demo_id: UUID,
        product_id: str | None = None,
        *,
        leave_bot: Callable[[str], None] | None = None,
    ) -> DemoHandle | None:
        handle = self.get(demo_id, product_id)
        if handle is None:
            return None
            
        owner = self._store.get_owner(demo_id)
        if owner and owner != self.worker_id:
            self._store.publish_stop(owner, demo_id)
        else:
            if demo_id in self._demos:
                self._demos[demo_id]._stop.set()
                
        if handle.bot_id:
            leave = leave_bot or self._leave_attendee_bot
            try:
                leave(handle.bot_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[runner] end: leave bot {handle.bot_id} failed: {exc}", flush=True)

        if handle.status in ("starting", "running"):
            handle.status = "finished"
            handle.error = None
            handle.finished_at = datetime.now(timezone.utc)
            self._store.save(handle)
        return handle"""
content = re.sub(r'    def stop\([\s\S]*?    @staticmethod\n    def _leave_attendee_bot', stop_code_repl + '\n\n    @staticmethod\n    def _leave_attendee_bot', content)

with open("navigator/app/runner.py", "w") as f:
    f.write(content)

