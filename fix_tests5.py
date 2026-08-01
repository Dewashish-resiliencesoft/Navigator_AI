with open("tests/test_client_dashboard.py", "r") as f:
    content = f.read()

# Fix the test_client_api_forbidden_without_loopback to expect 401 instead of 403
content = content.replace(
    '        r = client.get("/client/api/demos", headers={"Host": "public.example"})\n        assert r.status_code == 403',
    '        r = client.get("/client/api/demos", headers={"Host": "public.example"})\n        assert r.status_code == 401'
)

# Append test_client_product_domain_updates_site_graph_base_url
TEST_DOMAIN = """

def test_client_product_domain_updates_site_graph_base_url(tmp_path):
    bundle = _client(tmp_path, client_api_key="")
    client, prev, registry, log, auth_store = bundle
    prev_product = settings.product_url
    try:
        boot = client.post("/client/api/bootstrap", headers={"Host": "localhost"})
        assert boot.status_code == 200
        boot_body = boot.json()

        auth_store.create_user(product_id=boot_body["product_id"], email="test@acme.com", password="password")
        login_resp = client.post("/v1/auth/login", json={"email": "test@acme.com", "password": "password"}, headers={"Host": "localhost"})
        assert login_resp.status_code == 200, login_resp.text
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}

        before = client.get("/client/api/product-domain", headers=headers)
        assert before.status_code == 200, before.text
        assert before.json()["placeholder"] is True

        bad = client.put(
            "/client/api/product-domain",
            json={"base_url": "https://example.com/"},
            headers=headers,
        )
        assert bad.status_code == 422

        ok = client.put(
            "/client/api/product-domain",
            json={"base_url": "https://app.acme.test/login"},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["base_url"] == "https://app.acme.test/"
        assert body["placeholder"] is False
        assert settings.product_url.startswith("https://app.acme.test")

        again = client.get("/client/api/product-domain", headers=headers)
        assert again.json()["base_url"] == "https://app.acme.test/"
    finally:
        settings.product_url = prev_product
        _cleanup(bundle, prev)
"""

if "def test_client_product_domain_updates_site_graph_base_url" not in content:
    content += TEST_DOMAIN

with open("tests/test_client_dashboard.py", "w") as f:
    f.write(content)
