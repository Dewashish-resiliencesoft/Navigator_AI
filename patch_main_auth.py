import re
from pathlib import Path

with open("navigator/app/main.py", "r") as f:
    content = f.read()

# 1. Imports
auth_imports = """
import jwt
import bcrypt
from fastapi import Response, Cookie
from navigator.app.auth_store import AuthStore, AuthError, InvalidCredentials
"""
content = content.replace("from navigator.app.session_tokens", auth_imports + "\nfrom navigator.app.session_tokens")

# 2. Add _auth_store
auth_store_add = """
_auth_store = AuthStore(settings.db_path)

def get_auth_store() -> AuthStore:
    return _auth_store
"""
content = content.replace("def get_token_store() -> SessionTokenStore:\n    return _token_store", "def get_token_store() -> SessionTokenStore:\n    return _token_store\n" + auth_store_add)

# 3. Add dashboard_authed dependency
dashboard_authed_add = """
def dashboard_authed(
    registry: Annotated[Registry, Depends(get_registry)],
    authorization: Annotated[str | None, Header()] = None,
) -> Product:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "expected: Authorization: Bearer <jwt>")
    
    token = authorization.split(None, 1)[1].strip()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        product_id = payload.get("product_id")
        if not product_id:
            raise HTTPException(401, "invalid JWT payload")
        return registry.get(product_id)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token") from None
    except ProductNotFound:
        raise HTTPException(401, "product not found") from None

DashboardAuthedProduct = Annotated[Product, Depends(dashboard_authed)]
"""
content = content.replace("AuthedProduct = Annotated[Product, Depends(authed)]\n", "AuthedProduct = Annotated[Product, Depends(authed)]\n" + dashboard_authed_add)

# 4. Auth Models & Endpoints
auth_endpoints = """
class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    product_id: str

def _mint_jwt(user_id: str, product_id: str) -> str:
    now = datetime.now(timezone.utc)
    expires_in = 900
    payload = {
        "sub": user_id,
        "product_id": product_id,
        "role": "admin",
        "exp": int(now.timestamp() + expires_in)
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

@app.post("/v1/auth/login", response_model=TokenResponse)
def login(
    req: LoginRequest,
    response: Response,
    store: Annotated[AuthStore, Depends(get_auth_store)]
) -> dict:
    user = store.get_user_by_email(req.email)
    if not user or not bcrypt.checkpw(req.password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "invalid credentials")
    
    access_token = _mint_jwt(user["user_id"], user["product_id"])
    refresh_token = store.create_refresh_token(user["user_id"])
    
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=7 * 24 * 3600
    )
    
    return {
        "access_token": access_token,
        "expires_in": 900,
        "product_id": user["product_id"]
    }

@app.post("/v1/auth/refresh", response_model=TokenResponse)
def refresh(
    response: Response,
    store: Annotated[AuthStore, Depends(get_auth_store)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> dict:
    if not refresh_token:
        raise HTTPException(401, "no refresh token")
    
    try:
        user_id = store.consume_refresh_token(refresh_token)
        user = store.get_user(user_id)
        if not user:
            raise HTTPException(401, "invalid user")
            
        access_token = _mint_jwt(user["user_id"], user["product_id"])
        new_refresh_token = store.create_refresh_token(user["user_id"])
        
        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=7 * 24 * 3600
        )
        
        return {
            "access_token": access_token,
            "expires_in": 900,
            "product_id": user["product_id"]
        }
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from None

@app.post("/v1/auth/logout")
def logout(
    response: Response,
    store: Annotated[AuthStore, Depends(get_auth_store)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> dict:
    if refresh_token:
        store.revoke_refresh_token(refresh_token)
    response.delete_cookie("refresh_token")
    return {"ok": True}
"""
content = content.replace("class SessionTokenRequest(BaseModel):", auth_endpoints + "\nclass SessionTokenRequest(BaseModel):")

# 5. Find and replace require_local_ops in all client endpoints
# Example: 
# @app.get("/client/api/site-graph")
# def client_get_site_graph(request: Request, registry: Reg) -> dict:
#     require_local_ops(request)
#     product = _client_product(registry)
# Replacement:
# @app.get("/client/api/site-graph")
# def client_get_site_graph(product: DashboardAuthedProduct, registry: Reg) -> dict:
#     ...

# For GET /client/api/demos
content = re.sub(
    r'def client_list_demos\(request: Request, registry: Reg\)',
    'def client_list_demos(product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For GET /client/api/demos/{demo_id}
content = re.sub(
    r'def client_get_demo\([^)]+\)',
    'def client_get_demo(demo_id: UUID, product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For POST /client/api/demos/start
content = re.sub(
    r'def client_start_demo\([^)]+\)',
    'def client_start_demo(spec: NewDemo, product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For POST /client/api/demos/{demo_id}/end
content = re.sub(
    r'def client_end_demo\([^)]+\)',
    'def client_end_demo(demo_id: UUID, product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For GET /client/api/site-graph
content = re.sub(
    r'def client_get_site_graph\(request: Request, registry: Reg\)',
    'def client_get_site_graph(product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For PUT /client/api/site-graph
content = re.sub(
    r'def client_put_site_graph\(request: Request, body: SiteGraphBody, registry: Reg\)',
    'def client_put_site_graph(product: DashboardAuthedProduct, body: SiteGraphBody, registry: Reg)',
    content
)
# For GET /client/api/product-domain
content = re.sub(
    r'def client_get_product_domain\(request: Request, registry: Reg\)',
    'def client_get_product_domain(product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For PUT /client/api/product-domain
content = re.sub(
    r'def client_put_product_domain\(request: Request, body: ProductDomainBody, registry: Reg\)',
    'def client_put_product_domain(product: DashboardAuthedProduct, body: ProductDomainBody, registry: Reg)',
    content
)
# For GET /client/api/bio
content = re.sub(
    r'def client_get_bio\(request: Request, registry: Reg\)',
    'def client_get_bio(product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For PUT /client/api/bio
content = re.sub(
    r'def client_put_bio\(request: Request, body: BioBody, registry: Reg\)',
    'def client_put_bio(product: DashboardAuthedProduct, body: BioBody, registry: Reg)',
    content
)
# For GET /client/api/knowledge
content = re.sub(
    r'def client_get_knowledge\(request: Request, registry: Reg\)',
    'def client_get_knowledge(product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For PUT /client/api/knowledge
content = re.sub(
    r'def client_put_knowledge\(request: Request, body: KnowledgeBody, registry: Reg\)',
    'def client_put_knowledge(product: DashboardAuthedProduct, body: KnowledgeBody, registry: Reg)',
    content
)
# For GET /client/api/flows
content = re.sub(
    r'def client_get_flows\(request: Request, registry: Reg\)',
    'def client_get_flows(product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For PUT /client/api/flows
content = re.sub(
    r'def client_put_flows\(request: Request, body: FlowsBody, registry: Reg\)',
    'def client_put_flows(product: DashboardAuthedProduct, body: FlowsBody, registry: Reg)',
    content
)
# For GET /client/api/record
content = re.sub(
    r'def client_get_recorder_status\(request: Request, registry: Reg\)',
    'def client_get_recorder_status(product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For POST /client/api/record/start
content = re.sub(
    r'def client_start_recording\(request: Request, body: RecordStartBody, registry: Reg\)',
    'def client_start_recording(product: DashboardAuthedProduct, body: RecordStartBody, registry: Reg)',
    content
)
# For POST /client/api/record/stop
content = re.sub(
    r'def client_stop_recording\(request: Request, registry: Reg\)',
    'def client_stop_recording(product: DashboardAuthedProduct, registry: Reg)',
    content
)
# For GET /client/api/metrics
content = re.sub(
    r'def client_get_metrics\(request: Request, registry: Reg\)',
    'def client_get_metrics(product: DashboardAuthedProduct, registry: Reg)',
    content
)

content = re.sub(r'^\s*require_local_ops\(request\)\n', '', content, flags=re.MULTILINE)
content = re.sub(r'^\s*product = _client_product\(registry\)\n', '', content, flags=re.MULTILINE)

# Also handle client_bootstrap, let's keep it as is since it requires local ops
# but wait, client_bootstrap has require_local_ops, so we should keep require_local_ops for it if it wasn't stripped.
# The regex `require_local_ops(request)` might have stripped it from client_bootstrap. That's fine if we don't need bootstrap.
# Actually, if we are making a real SPA login, do we need `client_bootstrap`? It was only to issue a local key. We can leave it alone.
# The regex `require_local_ops(request)` will strip it from `client_bootstrap`. Let's restore it in `client_bootstrap`.

bootstrap_fixed = """@app.post("/client/api/bootstrap")
def client_bootstrap(request: Request, registry: Reg) -> dict:
    require_local_ops(request)
"""
content = re.sub(
    r'@app\.post\("/client/api/bootstrap"\)\ndef client_bootstrap\(request: Request, registry: Reg\) -> dict:',
    bootstrap_fixed,
    content
)

with open("navigator/app/main.py", "w") as f:
    f.write(content)
