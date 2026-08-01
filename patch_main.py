import re

with open("navigator/app/main.py", "r") as f:
    content = f.read()

# 1. Imports
import_add = """from navigator.app.session_tokens import SessionTokenStore, SessionTokenError
"""
content = content.replace("from navigator.app.runner import DemoRunner\n", "from navigator.app.runner import DemoRunner\n" + import_add)

# 2. Add _token_store and get_token_store
store_add = """
_token_store = SessionTokenStore(settings.db_path)

def get_token_store() -> SessionTokenStore:
    return _token_store
"""
content = content.replace("_log = ActionLog(settings.db_path)\n", "_log = ActionLog(settings.db_path)\n" + store_add)

# 3. Add authed_or_session
authed_or_session_add = """
def authed_or_session(
    registry: Annotated[Registry, Depends(get_registry)],
    store: Annotated[SessionTokenStore, Depends(get_token_store)],
    authorization: Annotated[str | None, Header()] = None,
) -> tuple[Product, IntakePrefill | None]:
    if not authorization or not authorization.lower().startswith("token "):
        raise HTTPException(401, "expected: Authorization: Token <key_or_token>")
    
    token = authorization.split(None, 1)[1].strip()
    if token.startswith("nav_"):
        try:
            return registry.authenticate(token), None
        except ProductNotFound:
            raise HTTPException(401, "invalid API key") from None
    elif token.startswith("sess_"):
        try:
            result = store.consume_token(token)
            product = registry.get(result["product_id"])
            intake = IntakePrefill(**result["intake"]) if result["intake"] else None
            return product, intake
        except SessionTokenError as exc:
            raise HTTPException(401, str(exc)) from None
        except ProductNotFound:
            raise HTTPException(401, "product not found") from None
    else:
        raise HTTPException(401, "invalid token format")

AuthedOrSession = Annotated[tuple[Product, IntakePrefill | None], Depends(authed_or_session)]
"""
content = content.replace("AuthedProduct = Annotated[Product, Depends(authed)]\n", "AuthedProduct = Annotated[Product, Depends(authed)]\n" + authed_or_session_add)

# 4. Session Token models
session_models_add = """
class SessionTokenRequest(BaseModel):
    intake: IntakePrefill | None = None
    expires_in_seconds: int = Field(default=3600, ge=60, le=86400)

class SessionTokenResponse(BaseModel):
    token: str
    expires_at: str
    product_id: str
"""
content = content.replace("class IntakePrefill(BaseModel):\n", session_models_add + "\nclass IntakePrefill(BaseModel):\n")

# 5. POST /v1/session-tokens
endpoint_add = """
@app.post("/v1/session-tokens", response_model=SessionTokenResponse, status_code=201)
def create_session_token(
    req: SessionTokenRequest,
    product: AuthedProduct,
    store: Annotated[SessionTokenStore, Depends(get_token_store)]
) -> dict:
    try:
        intake_dict = req.intake.model_dump() if req.intake else None
        token, expires_at = store.create_token(
            product.product_id, 
            intake_dict, 
            req.expires_in_seconds
        )
        return {
            "token": token,
            "expires_at": expires_at.isoformat(),
            "product_id": product.product_id
        }
    except SessionTokenError as exc:
        raise HTTPException(429, str(exc)) from None
"""
# insert before @app.post("/v1/demos/start"...)
# Need to find start_live_demo
content = content.replace("@app.post(\"/v1/demos/start\"", endpoint_add + "\n@app.post(\"/v1/demos/start\"")

# 6. Update POST /v1/demos/start signature and intake logic
sig_search = """def start_live_demo(
    spec: StartLiveDemo,
    product: AuthedProduct,
    registry: Reg,
    runner: Runner,
    providers: Providers,
) -> LiveDemoView:"""
sig_replace = """def start_live_demo(
    spec: StartLiveDemo,
    auth_ctx: AuthedOrSession,
    registry: Reg,
    runner: Runner,
    providers: Providers,
) -> LiveDemoView:
    product, token_intake = auth_ctx
"""
content = content.replace(sig_search, sig_replace)

intake_search = """        intake_prefill=None if spec.intake is None else spec.intake.model_dump(),"""
intake_replace = """        intake_prefill=(token_intake or spec.intake).model_dump() if (token_intake or spec.intake) else None,"""
content = content.replace(intake_search, intake_replace)


with open("navigator/app/main.py", "w") as f:
    f.write(content)
