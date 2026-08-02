# Navigator AI Client Embed

This directory provides a minimal scaffold for securely embedding a "Start a demo" button into your product's public landing page or dashboard.

## Security Overview

**NEVER expose your real `client_api_key` (`nav_...`) in client-side HTML or JavaScript.**

Instead, use **Session Tokens** (`sess_...`). A session token is scoped to start *exactly one demo* for your product and expires shortly after creation.

## How to Implement

### 1. Generate a Session Token (Server-Side)

On your backend, when a prospect lands on your page, make an authenticated call to your Navigator AI instance to mint a new token:

```bash
# Executed by your backend (e.g. Node.js, Python, Ruby)
curl -X POST https://your-navigator-instance.com/v1/session-tokens \
  -H "Authorization: Token nav_YOUR_REAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "intake": {
      "name": "Jane Prospect",
      "company": "Acme Corp"
    },
    "expires_in_seconds": 3600
  }'
```

**Response:**
```json
{
  "token": "sess_xYZ123...",
  "expires_at": "2026-08-01T23:59:59Z",
  "product_id": "your-product"
}
```

### 2. Embed the UI Scaffold (Client-Side)

Take the generated `sess_...` token and inject it into the `data-token` attribute of the `navigator.js` script tag in your HTML template:

```html
<!-- In your HTML template, e.g. EJS, Jinja, ERB -->
<script 
  src="path/to/navigator.js" 
  data-api-url="https://your-navigator-instance.com"
  data-token="{{ generated_session_token }}">
</script>
```

When the user clicks the "Start a demo" button rendered by this script, it will consume the session token and redirect them directly into the live demo meeting. If the user clicks the button twice or the token expires, the backend will safely reject it.
