from uuid import uuid4
import time
import pytest
from navigator.app.runner import DemoRunner
from navigator.core.schemas import Persona
from navigator.knowledge.site_graph import SiteGraph, PageSpec

def get_mock_graph():
    return SiteGraph(
        version="1",
        site="test_product",
        base_url="http://test",
        pages={"home": PageSpec(name="home", url="/", selectors={}, flows={"flow_1": ()})}
    )

def test_single_worker_lifecycle(tmp_path):
    runner = DemoRunner(str(tmp_path / "db.sqlite"), redis_url=None)
    graph = get_mock_graph()
    handle = runner.start("prod_1", graph, 1, ("home", "flow_1"), origin="dashboard_test")
    assert handle.demo_id is not None
    assert runner.get(handle.demo_id).status in ("starting", "running")
    
    runner.stop(handle.demo_id)
    assert runner.get(handle.demo_id).status == "finished"

def test_multi_worker_visibility_and_stop(tmp_path, monkeypatch):
    import fakeredis
    
    server = fakeredis.FakeServer()
    def fake_from_url(url, **kwargs):
        return fakeredis.FakeRedis(server=server, **kwargs)
        
    monkeypatch.setattr("redis.Redis.from_url", fake_from_url)
    
    # Setup two runners simulating two workers
    runner1 = DemoRunner(str(tmp_path / "db1.sqlite"), redis_url="redis://localhost/0")
    runner2 = DemoRunner(str(tmp_path / "db2.sqlite"), redis_url="redis://localhost/0")
    
    # Start on runner 1
    graph = get_mock_graph()
    handle = runner1.start("prod_1", graph, 1, ("home", "flow_1"), origin="dashboard_test")
    
    # Wait for sync loop to save to redis
    time.sleep(1.2)
    
    # Should be visible on runner 2
    remote_handle = runner2.get(handle.demo_id)
    assert remote_handle is not None
    assert remote_handle.product_id == "prod_1"
    
    # List works on runner 2
    lst = runner2.list("prod_1")
    assert len(lst) == 1
    assert lst[0].demo_id == handle.demo_id
    
    # Stop from runner 2
    # Mock leave_bot so it doesn't fail
    stopped = runner2.stop(handle.demo_id, leave_bot=lambda bot_id: None)
    assert stopped.status == "finished"
    
    # Verify runner 1 received the stop signal (it should flip local _stop)
    time.sleep(0.5) # allow pubsub message to arrive
    local_handle = runner1.get(handle.demo_id)
    assert local_handle._stop.is_set()
