from uuid import uuid4

from navigator.logs.transcript import MeetingTranscriptStore


def test_append_orders_and_scopes(tmp_path):
    sid = uuid4()
    with MeetingTranscriptStore(tmp_path / "t.db") as store:
        store.append(
            product_id="acme",
            session_id=sid,
            kind="screen",
            page_id="home",
            text="https://example.com/",
        )
        store.append(
            product_id="acme",
            session_id=sid,
            kind="agent",
            page_id="home",
            text="Welcome.",
        )
        store.append(
            product_id="other",
            session_id=sid,
            kind="agent",
            page_id="home",
            text="Leak.",
        )
        rows = store.for_session(sid, product_id="acme")
    assert [r.kind for r in rows] == ["screen", "agent"]
    assert rows[0].text.startswith("https://")
    assert rows[1].text == "Welcome."


def test_rejects_unknown_kind(tmp_path):
    with MeetingTranscriptStore(tmp_path / "t.db") as store:
        try:
            store.append(
                product_id="acme",
                session_id=uuid4(),
                kind="click",
                page_id="",
                text="nope",
            )
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_skips_blank_text(tmp_path):
    sid = uuid4()
    with MeetingTranscriptStore(tmp_path / "t.db") as store:
        row = store.append(
            product_id="acme",
            session_id=sid,
            kind="agent",
            page_id="",
            text="   ",
        )
        assert row is None
        assert store.for_session(sid, product_id="acme") == []


def test_transcript_api_returns_rows_for_product(tmp_path, monkeypatch):
    from test_client_dashboard import _cleanup, _client
    from test_api import ACME, register
    from navigator.core.settings import settings
    from navigator.logs.transcript import MeetingTranscriptStore

    db = tmp_path / "transcript_api.db"
    monkeypatch.setattr(settings, "db_path", str(db))

    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    try:
        p = register(client, "Acme Inbox", ACME)
        auth_store.create_user(
            product_id=p["id"], email="tx@acme.com", password="password"
        )
        login_resp = client.post(
            "/v1/auth/login",
            json={"email": "tx@acme.com", "password": "password"},
            headers={"Host": "localhost"},
        )
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}

        sid = uuid4()
        with MeetingTranscriptStore(db) as store:
            store.append(
                product_id=p["id"],
                session_id=sid,
                kind="agent",
                page_id="home",
                text="Hi from meeting.",
            )

        ok = client.get(f"/client/api/runs/{sid}/transcript", headers=headers)
        assert ok.status_code == 200, ok.text
        rows = ok.json()
        assert len(rows) == 1
        assert rows[0]["kind"] == "agent"
        assert rows[0]["text"] == "Hi from meeting."

        missing = client.get(
            f"/client/api/runs/{uuid4()}/transcript", headers=headers
        )
        assert missing.status_code == 404
    finally:
        _cleanup(bundle, prev)


def test_emit_swallows_errors(tmp_path, monkeypatch):
    from navigator.logs import transcript_emit as te

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(te, "MeetingTranscriptStore", _boom)
    te.emit_transcript(
        product_id="acme",
        session_id=uuid4(),
        kind="agent",
        text="hi",
        db_path=str(tmp_path / "x.db"),
    )


def test_classify_agent_reply_after_visitor(tmp_path):
    from navigator.logs.transcript_emit import classify_agent_kind

    sid = uuid4()
    db = tmp_path / "t.db"
    with MeetingTranscriptStore(db) as store:
        store.append(
            product_id="acme",
            session_id=sid,
            kind="visitor",
            text="What is this?",
        )
    assert classify_agent_kind(sid, "acme", db_path=str(db)) == "agent_reply"
    assert (
        classify_agent_kind(sid, "acme", db_path=str(tmp_path / "empty.db"))
        == "agent"
    )
