with open("navigator/app/main.py", "r") as f:
    content = f.read()

# 1. datetime
if "from datetime import datetime, timezone" not in content:
    content = content.replace(
        "from uuid import UUID",
        "from uuid import UUID\nfrom datetime import datetime, timezone"
    )

# 2. client_start_live_demo
OLD_START = """def client_start_live_demo(
    request: Request,
    spec: StartLiveDemo,
    registry: Reg,
    runner: Runner,
    providers: Providers,
) -> LiveDemoView:
    require_local_ops(request)
    return start_live_demo(spec, _client_product(registry), registry, runner, providers)"""
NEW_START = """def client_start_live_demo(
    request: Request,
    spec: StartLiveDemo,
    registry: Reg,
    runner: Runner,
    providers: Providers,
) -> LiveDemoView:
    require_local_ops(request)
    return start_live_demo(spec, (_client_product(registry), None), registry, runner, providers)"""
content = content.replace(OLD_START, NEW_START)

# 3. client_put_product_domain
OLD_PUT_DOMAIN = """@app.put("/client/api/product-domain")
def client_put_product_domain(
    request: Request, body: ProductDomainBody, registry: Reg
) -> dict:"""
NEW_PUT_DOMAIN = """@app.put("/client/api/product-domain")
def client_put_product_domain(
    product: DashboardAuthedProduct, request: Request, body: ProductDomainBody, registry: Reg
) -> dict:"""
content = content.replace(OLD_PUT_DOMAIN, NEW_PUT_DOMAIN)

with open("navigator/app/main.py", "w") as f:
    f.write(content)
