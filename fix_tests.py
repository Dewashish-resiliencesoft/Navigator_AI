import re

with open("tests/test_client_dashboard.py", "r") as f:
    content = f.read()

# 1. Add AuthStore to _client
content = content.replace(
    "from navigator.logs.store import ActionLog",
    "from navigator.logs.store import ActionLog\nfrom navigator.app.auth_store import AuthStore"
)

content = content.replace(
    "    registry = Registry(tmp_path / \"registry.db\")\n    log = ActionLog(tmp_path / \"actions.db\")",
    "    registry = Registry(tmp_path / \"registry.db\")\n    log = ActionLog(tmp_path / \"actions.db\")\n    auth_store = AuthStore(tmp_path / \"auth.db\")"
)

content = content.replace(
    "    app_module.app.dependency_overrides[app_module.get_log] = lambda: log",
    "    app_module.app.dependency_overrides[app_module.get_log] = lambda: log\n    app_module.app.dependency_overrides[app_module.get_auth_store] = lambda: auth_store"
)

content = content.replace(
    "    client.registry = registry\n    return client, prev, registry, log",
    "    client.registry = registry\n    return client, prev, registry, log, auth_store"
)

content = content.replace(
    "    _, _, registry, log = client_bundle",
    "    _, _, registry, log, auth_store = client_bundle"
)

content = content.replace(
    "    registry.close()\n    log.close()",
    "    registry.close()\n    log.close()\n    auth_store.close()"
)

# 2. Update the tests to use JWT

# Helper logic for login
JWT_LOGIC = """        # register() returns headers with Token; extract key
        key = p["headers"]["Authorization"].split(None, 1)[1]
        
        login_resp = client.post("/client/api/auth/login", json={"product_id": p["id"], "api_key": key}, headers={"Host": "localhost"})
        assert login_resp.status_code == 200, login_resp.text
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}"""

content = content.replace(
    '        # register() returns headers with Token; extract key\n        key = p["headers"]["Authorization"].split(None, 1)[1]\n        settings.client_api_key = key',
    JWT_LOGIC
)

# Replace settings.client_api_key = prev  and closes in finally
content = content.replace(
    "    finally:\n        settings.client_api_key = prev\n        app_module.app.dependency_overrides.clear()\n        registry.close()\n        log.close()",
    "    finally:\n        settings.client_api_key = prev\n        app_module.app.dependency_overrides.clear()\n        registry.close()\n        log.close()\n        auth_store.close()"
)

# Replace client_api_key unpacking
content = content.replace(
    "    client, prev, registry, log = bundle",
    "    client, prev, registry, log, auth_store = bundle"
)

# Replace headers={"Host": "localhost"} with headers=headers for authenticated routes
# test_client_api_start_uses_server_key
content = content.replace(
    '        r = client.post(\n            "/client/api/demos/start",\n            json={\n                "platform": "zoom",\n                "page_id": "main",\n                "flow_id": "happy_path",\n                "intake": {"name": "Dewa", "company": "Acme"},\n            },\n            headers={"Host": "localhost"},\n        )',
    '        r = client.post(\n            "/client/api/demos/start",\n            json={\n                "platform": "zoom",\n                "page_id": "main",\n                "flow_id": "happy_path",\n                "intake": {"name": "Dewa", "company": "Acme"},\n            },\n            headers=headers,\n        )'
)

# test_client_bio_and_knowledge_roundtrip
content = content.replace(
    '        r = client.put(\n            "/client/api/bio",\n            json={\n                "fields": [\n                    {"key": "company_name", "label": "Company name", "value": "Acme Co"},\n                    {"key": "about", "label": "About", "value": "We sell widgets"},\n                ]\n            },\n            headers={"Host": "localhost"},\n        )',
    '        r = client.put(\n            "/client/api/bio",\n            json={\n                "fields": [\n                    {"key": "company_name", "label": "Company name", "value": "Acme Co"},\n                    {"key": "about", "label": "About", "value": "We sell widgets"},\n                ]\n            },\n            headers=headers,\n        )'
)
content = content.replace(
    '        got = client.get("/client/api/bio", headers={"Host": "localhost"})',
    '        got = client.get("/client/api/bio", headers=headers)'
)
content = content.replace(
    '        k = client.put(\n            "/client/api/knowledge",\n            json={"markdown": "# Tone\\nBe warm.\\n"},\n            headers={"Host": "localhost"},\n        )',
    '        k = client.put(\n            "/client/api/knowledge",\n            json={"markdown": "# Tone\\nBe warm.\\n"},\n            headers=headers,\n        )'
)

# test_client_flows_playlist_save
content = content.replace(
    '        r = client.put(\n            "/client/api/flows",\n            json={\n                "playlist": [\n                    {\n                        "order": 1,\n                        "name": "Happy path",\n                        "page_id": "main",\n                        "flow_id": "happy_path",\n                    }\n                ]\n            },\n            headers={"Host": "localhost"},\n        )',
    '        r = client.put(\n            "/client/api/flows",\n            json={\n                "playlist": [\n                    {\n                        "order": 1,\n                        "name": "Happy path",\n                        "page_id": "main",\n                        "flow_id": "happy_path",\n                    }\n                ]\n            },\n            headers=headers,\n        )'
)
content = content.replace(
    '        listed = client.get("/client/api/flows", headers={"Host": "localhost"})',
    '        listed = client.get("/client/api/flows", headers=headers)'
)

# test_client_bootstrap_sets_key does NOT use register(), it tests /bootstrap
# Wait, bootstrap now returns product_id and api_key? Let's check main.py /bootstrap endpoint.
with open("tests/test_client_dashboard.py", "w") as f:
    f.write(content)
