with open("tests/test_client_dashboard.py", "r") as f:
    content = f.read()

# Fix the unpacking in bootstrap tests
content = content.replace(
    'client, prev, *_ = bundle',
    'client, prev, registry, log, auth_store = bundle'
)

# Add login to test_client_bio_and_knowledge_roundtrip
OLD_BIO = """        p = register(client, "Acme Inbox", ACME)
        key = p["headers"]["Authorization"].split(None, 1)[1]
        settings.client_api_key = key"""
NEW_BIO = """        p = register(client, "Acme Inbox", ACME)
        key = p["headers"]["Authorization"].split(None, 1)[1]
        auth_store.create_user(product_id=p["id"], email="test@acme.com", password="password")
        login_resp = client.post("/v1/auth/login", json={"email": "test@acme.com", "password": "password"}, headers={"Host": "localhost"})
        assert login_resp.status_code == 200, login_resp.text
        jwt = login_resp.json()["access_token"]
        headers = {"Host": "localhost", "Authorization": f"Bearer {jwt}"}"""
content = content.replace(OLD_BIO, NEW_BIO)

with open("tests/test_client_dashboard.py", "w") as f:
    f.write(content)
