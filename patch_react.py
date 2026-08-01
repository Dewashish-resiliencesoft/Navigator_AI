import re

with open("navigator/client/web/src/lib/api.ts", "r") as f:
    api_ts = f.read()

auth_logic = """let _accessToken: string | null = null;
let _refreshPromise: Promise<void> | null = null;

export function setAccessToken(token: string | null) {
  _accessToken = token;
}

export function getAccessToken() {
  return _accessToken;
}

async function doRefresh() {
  const res = await fetch("/v1/auth/refresh", { method: "POST" });
  if (!res.ok) {
    _accessToken = null;
    throw new ApiError("Session expired", 401);
  }
  const data = await res.json();
  _accessToken = data.access_token;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!_accessToken && path !== "/v1/auth/login" && path !== "/v1/auth/refresh") {
    if (!_refreshPromise) _refreshPromise = doRefresh().finally(() => { _refreshPromise = null; });
    await _refreshPromise;
  }

  const doRequest = async () => {
    const headers: Record<string, string> = { "Content-Type": "application/json", ...((init?.headers as Record<string, string>) ?? {}) };
    if (_accessToken) {
      headers["Authorization"] = `Bearer ${_accessToken}`;
    }
    const res = await fetch(path, { ...init, headers });
    
    if (res.status === 401 && path !== "/v1/auth/login" && path !== "/v1/auth/refresh") {
      throw new ApiError("unauthorized", 401);
    }
    
    const text = await res.text();
    let body: any = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = { detail: text }; }
    
    if (!res.ok) {
      const detail = body?.detail ?? body?.message ?? text ?? res.statusText;
      throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), res.status);
    }
    return body as T;
  };

  try {
    return await doRequest();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      if (!_refreshPromise) _refreshPromise = doRefresh().finally(() => { _refreshPromise = null; });
      await _refreshPromise;
      return await doRequest();
    }
    throw err;
  }
}

export async function login(email: string, password: string) {
  const data = await request<{ access_token: string }>("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password })
  });
  _accessToken = data.access_token;
}

export async function logout() {
  try {
    await request("/v1/auth/logout", { method: "POST" });
  } catch (e) {}
  _accessToken = null;
}

const get = <T>(path: string) => request<T>(path);
const send = <T>(path: string, method: string, body?: unknown) =>
  request<T>(path, { method, body: JSON.stringify(body ?? {}) });
"""

# Replace everything from async function request down to const get
api_ts = re.sub(
    r'async function request<T>.*?const get = <T>\(path: string\) => withBootstrap\(\(\) => request<T>\(path\)\);\s*const send = <T>\(path: string, method: string, body\?: unknown\) =>\s*withBootstrap\(\(\) =>\s*request<T>\(path, \{ method, body: JSON.stringify\(body \?\? \{\}\) \}\),\s*\);',
    auth_logic,
    api_ts,
    flags=re.DOTALL
)

# Add login, logout, checkAuth to exported api object
api_ts = api_ts.replace('export const api = {', 'export const api = {\n  login,\n  logout,\n  checkAuth: async () => { if (!_accessToken) await request("/client/api/bio").catch(() => {}); return !!_accessToken; },')

with open("navigator/client/web/src/lib/api.ts", "w") as f:
    f.write(api_ts)

with open("navigator/client/web/src/App.tsx", "r") as f:
    app_tsx = f.read()

login_component = """
import { api } from "./lib/api";

function Login({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.login(email, password);
      onLogin();
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm rounded-xl border p-6 shadow-xl" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        <h2 className="mb-6 text-2xl font-bold">Log In to Navigator</h2>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="mb-1 block text-sm text-[var(--muted)]">Email</label>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)} required className="w-full rounded-md border p-2" style={{ borderColor: "var(--line)", background: "var(--bg)", color: "var(--text)" }} />
          </div>
          <div>
            <label className="mb-1 block text-sm text-[var(--muted)]">Password</label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} required className="w-full rounded-md border p-2" style={{ borderColor: "var(--line)", background: "var(--bg)", color: "var(--text)" }} />
          </div>
          {error && <div className="text-sm text-red-500">{error}</div>}
          <button type="submit" disabled={loading} className="mt-2 rounded-md bg-blue-600 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {loading ? "Logging in..." : "Log In"}
          </button>
        </form>
      </div>
    </div>
  );
}

"""

app_tsx = app_tsx.replace("export default function App() {", login_component + "\nexport default function App() {\n  const [authed, setAuthed] = useState<boolean | null>(null);\n  useEffect(() => { api.checkAuth().then(ok => setAuthed(ok)); }, []);\n  if (authed === null) return null;\n  if (authed === false) return <Login onLogin={() => setAuthed(true)} />;")

with open("navigator/client/web/src/App.tsx", "w") as f:
    f.write(app_tsx)
