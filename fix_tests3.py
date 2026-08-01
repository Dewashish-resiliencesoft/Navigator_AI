import re

with open("tests/test_client_dashboard.py", "r") as f:
    content = f.read()

# Replace the login logic
OLD_LOGIN = 'login_resp = client.post("/v1/auth/login", json={"product_id": p["id"], "api_key": key}, headers={"Host": "localhost"})'
NEW_LOGIN = 'auth_store.create_user(product_id=p["id"], email="test@acme.com", password="password")\n        login_resp = client.post("/v1/auth/login", json={"email": "test@acme.com", "password": "password"}, headers={"Host": "localhost"})'
content = content.replace(OLD_LOGIN, NEW_LOGIN)

# For the bootstrap tests
OLD_BOOTSTRAP_LOGIN = 'login_resp = client.post("/v1/auth/login", json={"product_id": body["product_id"], "api_key": body["api_key"]}, headers={"Host": "localhost"})'
NEW_BOOTSTRAP_LOGIN = 'auth_store.create_user(product_id=body["product_id"], email="test@acme.com", password="password")\n        login_resp = client.post("/v1/auth/login", json={"email": "test@acme.com", "password": "password"}, headers={"Host": "localhost"})'
content = content.replace(OLD_BOOTSTRAP_LOGIN, NEW_BOOTSTRAP_LOGIN)

OLD_PRODUCT_DOMAIN_LOGIN = 'login_resp = client.post("/v1/auth/login", json={"product_id": boot_body["product_id"], "api_key": boot_body["api_key"]}, headers={"Host": "localhost"})'
NEW_PRODUCT_DOMAIN_LOGIN = 'auth_store.create_user(product_id=boot_body["product_id"], email="test@acme.com", password="password")\n        login_resp = client.post("/v1/auth/login", json={"email": "test@acme.com", "password": "password"}, headers={"Host": "localhost"})'
content = content.replace(OLD_PRODUCT_DOMAIN_LOGIN, NEW_PRODUCT_DOMAIN_LOGIN)

with open("tests/test_client_dashboard.py", "w") as f:
    f.write(content)
