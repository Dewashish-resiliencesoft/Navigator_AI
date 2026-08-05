# How to start Navigator AI

Clear step-by-step instructions to get the app running on your machine.
For architecture and features, see [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md).
For Google Meet credentials, see [`GOOGLE_MEET_SETUP.md`](GOOGLE_MEET_SETUP.md).

---

## What runs where

| Service | Port | URL | Required for |
|---------|------|-----|--------------|
| **Navigator API + dashboard** | 8000 | http://127.0.0.1:8000/client | Everything |
| **Attendee API** | 8002 | http://localhost:8002 | Live meeting demos |
| **Attendee webpage-streamer** | 8001 | (internal) | Screenshare in Meet |

**Two modes:**

| Mode | Start | You can |
|------|-------|---------|
| **A — Navigator only** | Steps 1–5 below | Dashboard, site graph, browser test demos (no video call) |
| **B — Full stack** | Steps 1–5 + Attendee (step 6) | Live Meet demo with voice + screenshare |

---

## First-time setup (do once)

### Step 1 — Python environment

**Windows (PowerShell):**

```powershell
cd C:\path\to\Navigator_AI

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
python -m playwright install chromium
```

**Linux / macOS:**

```bash
cd ~/Navigator_AI

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
python -m playwright install chromium
```

---

### Step 2 — Environment file

**Windows:**

```powershell
copy .env.example .env
notepad .env
```

**Linux / macOS:**

```bash
cp .env.example .env
```

Minimum to boot the dashboard:

```env
NAVIGATOR_HEADFUL=1
NAVIGATOR_DB_PATH=navigator.db
NAVIGATOR_REGISTRY_DB=registry.db
NAVIGATOR_JWT_SECRET=change-me-for-local-dev
```

For **live meeting demos**, also set (see `.env.example`):

```env
NAVIGATOR_GROQ_API_KEY=...
NAVIGATOR_FISH_API_KEY=...              # or Piper voice settings
NAVIGATOR_ATTENDEE_BASE_URL=http://localhost:8002/api/v1
NAVIGATOR_ATTENDEE_API_KEY=...          # from Attendee dashboard
NAVIGATOR_CREDENTIAL_KEY=...            # if saving product logins
```

Generate Fernet key for credential vault:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

### Step 3 — Build the dashboard UI

FastAPI serves the **built** React app. Without this, `/client` shows
"Console not built".

```bash
cd navigator/client/web
npm install
npm run build
cd ../../..
```

---

### Step 4 — (Optional) Attendee for live demos

Only needed for Google Meet / Zoom demos with voice and screenshare.

1. **Install Docker Desktop** and ensure it is running.

2. **Clone Attendee** (separate repo — not inside Navigator):

   ```bash
   git clone https://github.com/attendee-labs/attendee.git ~/projects/attendee
   cd ~/projects/attendee
   ```

3. **Create `.env`** (first time only):

   **Linux / Mac:**

   ```bash
   docker compose -f dev.docker-compose.yaml run --rm attendee-app-local python init_env.py > .env
   ```

   **Windows (PowerShell):**

   ```powershell
   docker compose -f dev.docker-compose.yaml run --rm attendee-app-local python init_env.py | Out-File -Encoding utf8 .env
   ```

   If `DJANGO_SECRET_KEY` contains `$`, wrap the value in quotes and escape
   `$` as `$$`.

4. **Use local port overrides** when Navigator already uses `:8000`.
   Save this as `local.docker-compose.yaml` in the Attendee repo if you do not
   have it yet:

   ```yaml
   services:
     attendee-app-local:
       ports: !override
         - "8002:8000"
     attendee-worker-local:
       entrypoint: []
     attendee-webpage-streamer-local:
       entrypoint: []
       security_opt: !override []
   ```

5. **Start Attendee:**

   ```bash
   docker compose -f dev.docker-compose.yaml -f local.docker-compose.yaml \
       --profile webpage-streamer up -d
   ```

   **Both `-f` flags** and **`--profile webpage-streamer`** are required.

6. **Sign up + API key:**
   - Open http://localhost:8002/accounts/signup/ (not `:8000`)
   - Create account → **Settings → API keys** → copy key
   - Paste into Navigator `.env` as `NAVIGATOR_ATTENDEE_API_KEY=`

---

## Every time you start the app

### Step 5 — Start Navigator

Open a terminal in the repo root. Activate venv, then run:

**Windows:**

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn navigator.app.main:app --reload --workers 1
```

**Linux / macOS:**

```bash
source .venv/bin/activate
uvicorn navigator.app.main:app --reload --workers 1
```

| Flag | Why |
|------|-----|
| `--reload` | Auto-restart on code changes (optional; omit in production) |
| `--workers 1` | **Required** — live demo state lives in one process |

Leave this terminal open. Server runs until you press `Ctrl+C`.

---

### Step 6 — Start Attendee (live demos only)

In a **second terminal**:

```bash
cd ~/projects/attendee
docker compose -f dev.docker-compose.yaml -f local.docker-compose.yaml \
    --profile webpage-streamer up -d
```

Check containers:

```bash
docker compose -f dev.docker-compose.yaml -f local.docker-compose.yaml ps
```

Expect: `attendee-app-local`, `attendee-worker-local`,
`attendee-webpage-streamer-local`, `postgres`, `redis` — all **Up**.

---

### Step 7 — Open the app

| What | URL |
|------|-----|
| **Client dashboard** | http://127.0.0.1:8000/client |
| Health check | http://127.0.0.1:8000/healthz |
| API docs | http://127.0.0.1:8000/docs |

**First visit:** click **Sign up** → company name, email, password → you land
in the dashboard.

**Must use `127.0.0.1` or `localhost`** — dashboard rejects other Host headers.

---

### Step 8 — Run a demo

1. Dashboard → **Live Demo** tab
2. Set product domain and meeting platform
3. Click **Start test demo**
4. For Meet demos: open the printed Meet link in another browser tab

Test demos use `origin: dashboard_test` — they do not count toward billing.

---

## Optional — edit dashboard UI live

Only when changing React/Tailwind code. Navigator API must already run on `:8000`.

```bash
cd navigator/client/web
npm run dev
```

Vite dev server proxies API calls to `:8000`. Rebuild with `npm run build` before
deploying or when not using Vite.

---

## Quick checklist

Copy this for daily use:

```
[ ] Docker Desktop running          (live demos only)
[ ] Attendee stack up on :8002      (live demos only)
[ ] venv activated
[ ] uvicorn … --workers 1           (port 8000)
[ ] Browser → http://127.0.0.1:8000/client
```

---

## Verify everything is up

**Navigator:**

```powershell
# Windows PowerShell
Invoke-WebRequest http://127.0.0.1:8000/healthz
```

```bash
# Linux / macOS
curl http://127.0.0.1:8000/healthz
```

**Attendee:**

```powershell
Invoke-WebRequest http://127.0.0.1:8002/
```

```bash
curl -I http://127.0.0.1:8002/
```

---

## Stop the app

| What | How |
|------|-----|
| Navigator | `Ctrl+C` in the uvicorn terminal |
| Attendee | `cd ~/projects/attendee` then `docker compose -f dev.docker-compose.yaml -f local.docker-compose.yaml down` |
| Vite dev server | `Ctrl+C` in the npm terminal |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `/client` says "Console not built" | Run `npm install && npm run build` in `navigator/client/web` |
| `uvicorn` not found | Activate `.venv` first |
| Port 8000 already in use | Stop other process or use `--port 8001` (not recommended — docs assume 8000) |
| Live demo: Attendee unreachable | Start Docker stack; confirm http://localhost:8002 responds |
| Attendee signup links show `:8000` | Open same path on `:8002` instead |
| Demo starts but no screenshare | Ensure `--profile webpage-streamer` was used; check webpage-streamer container is Up |
| `bash\r` / container exit 127 on Windows | Use `local.docker-compose.yaml` with `entrypoint: []` on worker/streamer |
| Meet demo fails at create meeting | Complete [`GOOGLE_MEET_SETUP.md`](GOOGLE_MEET_SETUP.md) (SA + DWD) |

---

## Run tests

From repo root with venv active:

```bash
python -m pytest -q
```

---

## Related docs

| Doc | Contents |
|-----|----------|
| [`ONBOARDING.md`](ONBOARDING.md) | Stack overview, port map, gotchas |
| [`GOOGLE_MEET_SETUP.md`](GOOGLE_MEET_SETUP.md) | Meet API, auto-join, screenshare config |
| [`PRODUCT_MODEL.md`](PRODUCT_MODEL.md) | Roles, test vs live demo, auth rules |
| [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md) | Full architecture and feature list |
