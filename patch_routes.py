import re

with open("navigator/app/main.py", "r") as f:
    content = f.read()

# 1. Update /client/api/demos/start
OLD_START = """@app.post("/client/api/demos/start", response_model=LiveDemoView, status_code=202)
def client_start_live_demo(
    request: Request,
    spec: StartLiveDemo,
    registry: Reg,
    runner: Runner,
    providers: Providers,
) -> LiveDemoView:
    return start_live_demo(spec, (_client_product(registry), None), registry, runner, providers)"""
NEW_START = """@app.post("/client/api/demos/start", response_model=LiveDemoView, status_code=202)
def client_start_live_demo(
    product: DashboardAuthedProduct,
    spec: StartLiveDemo,
    registry: Reg,
    runner: Runner,
    providers: Providers,
) -> LiveDemoView:
    return start_live_demo(spec, (product, None), registry, runner, providers)"""
content = content.replace(OLD_START, NEW_START)

# 2. Update /client/api/demos
OLD_LIST = """@app.get("/client/api/demos", response_model=list[DemoView])
def client_list_demos(request: Request, registry: Reg, runner: Runner) -> list[DemoView]:
    return list_demos(_client_product(registry), runner)"""
NEW_LIST = """@app.get("/client/api/demos", response_model=list[DemoView])
def client_list_demos(product: DashboardAuthedProduct, runner: Runner) -> list[DemoView]:
    return list_demos(product, runner)"""
content = content.replace(OLD_LIST, NEW_LIST)

# 3. Update /client/api/demos/{demo_id}
OLD_GET = """@app.get("/client/api/demos/{demo_id}", response_model=DemoView)
def client_get_demo(demo_id: UUID, product: DashboardAuthedProduct, registry: Reg) -> DemoView:
    return get_demo(demo_id, _client_product(registry), runner)"""
NEW_GET = """@app.get("/client/api/demos/{demo_id}", response_model=DemoView)
def client_get_demo(demo_id: UUID, product: DashboardAuthedProduct, runner: Runner) -> DemoView:
    return get_demo(demo_id, product, runner)"""
content = content.replace(OLD_GET, NEW_GET)

# 4. Update /client/api/demos/{demo_id}/end
OLD_END = """@app.post("/client/api/demos/{demo_id}/end", response_model=DemoView)
def client_end_demo(demo_id: UUID, product: DashboardAuthedProduct, registry: Reg) -> DemoView:
    return end_demo(demo_id, _client_product(registry), runner)"""
NEW_END = """@app.post("/client/api/demos/{demo_id}/end", response_model=DemoView)
def client_end_demo(demo_id: UUID, product: DashboardAuthedProduct, runner: Runner) -> DemoView:
    return end_demo(demo_id, product, runner)"""
content = content.replace(OLD_END, NEW_END)

# 5. Add product-domain routes
ROUTES = """

class ProductDomainBody(BaseModel):
    base_url: str = Field(min_length=1)

@app.get("/client/api/product-domain")
def client_get_product_domain(product: DashboardAuthedProduct, registry: Reg) -> dict:
    try:
        graph = registry.load_graph(product.product_id)
    except ProductNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {
        "base_url": graph.base_url,
        "placeholder": "example.com" in (graph.base_url or "").lower(),
    }

@app.put("/client/api/product-domain")
def client_put_product_domain(
    product: DashboardAuthedProduct, request: Request, body: ProductDomainBody, registry: Reg
) -> dict:
    try:
        rev = registry.get_revision(product.product_id)
        yaml_text = apply_base_url_to_yaml(rev.yaml, body.base_url)
        rev = registry.put_site_graph(product.product_id, yaml_text, "yaml")
        graph = registry.load_graph(product.product_id)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from None
    return {
        "ok": True,
        "base_url": graph.base_url,
        "revision": rev.revision,
        "placeholder": "example.com" in (graph.base_url or "").lower(),
    }
"""

if "def client_get_product_domain" not in content:
    content = content.replace(
        'class BioBody(BaseModel):',
        ROUTES.strip() + '\n\nclass BioBody(BaseModel):'
    )

with open("navigator/app/main.py", "w") as f:
    f.write(content)
