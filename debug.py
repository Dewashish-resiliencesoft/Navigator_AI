with open("navigator/app/main.py", "r") as f:
    content = f.read()

debug_code = """
@app.get("/client/api/product-domain")
def client_get_product_domain(product: DashboardAuthedProduct, registry: Reg) -> dict:
    print("DEBUG product:", product.model_dump())
    try:
        graph = registry.load_graph(product.product_id)
        print("DEBUG graph:", graph.site)
    except Exception as exc:
        print("DEBUG exc:", exc)
        raise HTTPException(404, str(exc)) from None
    return {
        "base_url": graph.base_url,
        "placeholder": "example.com" in (graph.base_url or "").lower(),
    }
"""

import re
content = re.sub(
    r'@app.get\("/client/api/product-domain"\)\ndef client_get_product_domain.*?\}',
    debug_code.strip(),
    content,
    flags=re.DOTALL
)

with open("navigator/app/main.py", "w") as f:
    f.write(content)
