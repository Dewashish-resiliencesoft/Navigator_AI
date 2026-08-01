import re

with open("tests/test_client_dashboard.py", "r") as f:
    content = f.read()

# Replace the bootstrap test
OLD_BOOTSTRAP = """    try:
        r = client.post("/client/api/bootstrap", headers={"Host": "localhost"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["api_key"].startswith("nav_")
        assert settings.client_api_key == body["api_key"]
        listed = client.get("/client/api/demos", headers={"Host": "localhost"})
        assert listed.status_code == 200"""
NEW_BOOTSTRAP = """    try:
        r = client.post("/client/api/bootstrap", headers={"Host": "localhost"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["api_key"].startswith("nav_")
        assert settings.client_api_key == body["api_key"]
        
        login_resp = client.post("/client/api/auth/login", json={"product_id": body["product_id"], "api_key": body["api_key"]}, headers={"Host": "localhost"})
        assert login_resp.status_code == 200, login_resp.text
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}
        
        listed = client.get("/client/api/demos", headers=headers)
        assert listed.status_code == 200"""

content = content.replace(OLD_BOOTSTRAP, NEW_BOOTSTRAP)

# Replace the product domain test
OLD_PRODUCT_DOMAIN = """    try:
        boot = client.post("/client/api/bootstrap", headers={"Host": "localhost"})
        assert boot.status_code == 200
        before = client.get("/client/api/product-domain", headers={"Host": "localhost"})
        assert before.status_code == 200
        assert before.json()["placeholder"] is True

        bad = client.put(
            "/client/api/product-domain",
            json={"base_url": "https://example.com/"},
            headers={"Host": "localhost"},
        )
        assert bad.status_code == 422

        ok = client.put(
            "/client/api/product-domain",
            json={"base_url": "https://app.acme.test/login"},
            headers={"Host": "localhost"},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["base_url"] == "https://app.acme.test/"
        assert body["placeholder"] is False
        assert settings.product_url.startswith("https://app.acme.test")

        again = client.get("/client/api/product-domain", headers={"Host": "localhost"})
        assert again.json()["base_url"] == "https://app.acme.test/"
    finally:"""
NEW_PRODUCT_DOMAIN = """    try:
        boot = client.post("/client/api/bootstrap", headers={"Host": "localhost"})
        assert boot.status_code == 200
        boot_body = boot.json()
        
        login_resp = client.post("/client/api/auth/login", json={"product_id": boot_body["product_id"], "api_key": boot_body["api_key"]}, headers={"Host": "localhost"})
        assert login_resp.status_code == 200, login_resp.text
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}
        
        before = client.get("/client/api/product-domain", headers=headers)
        assert before.status_code == 200
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
    finally:"""

content = content.replace(OLD_PRODUCT_DOMAIN, NEW_PRODUCT_DOMAIN)

with open("tests/test_client_dashboard.py", "w") as f:
    f.write(content)

