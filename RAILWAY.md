![Hermes Agent](https://raw.githubusercontent.com/NousResearch/hermes-agent/main/assets/banner.png)

# Deploy Your Own Hermes Agent

Your own AI agent, running 24/7, talking to you on Telegram. Think OpenClaw, but **better**. Hermes Agent by Nous Research comes with tool use, persistent memory, scheduled tasks, and multi-platform messaging — and this template gives you the whole thing with one click.

## About

No YAML files. No SSH. No "just clone the repo and figure it out." This template gives you a fully managed Hermes Agent accessible from your browser. Add API keys, connect messaging platforms, manage sessions, view analytics, and schedule cron jobs — all from the dashboard. The messaging gateway runs alongside it and automatically restarts when you change settings. Attach a Railway volume and your data sticks around forever.

**New:** File Browser is now embedded in the same container! Access all your agent's files directly through a web UI at port 8080 — no need to detach and reattach volumes between services.

## Getting Started

### 1. Deploy to Railway

Click the **Deploy on Railway** button, set the environment variables described in [Authentication](#authentication) below, and deploy. Once it's live, open your Railway-provided URL and sign in with the configured provider.

### 2. Add an LLM Provider

Your agent needs an AI model to work. [OpenRouter](https://openrouter.ai/) is the easiest option since it gives access to all major models with a single key.

1. Create an account at [openrouter.ai](https://openrouter.ai/) and generate an API key
2. In the Hermes dashboard, go to the **API Keys** page
3. Paste your key into the `OPENROUTER_API_KEY` field and save

### 3. Set Up a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts to name your bot
3. BotFather will give you a bot token — copy it
4. In the Hermes dashboard, go to the **API Keys** page
5. Paste the token into the `TELEGRAM_BOT_TOKEN` field and save
6. Also set `GATEWAY_ALLOW_ALL_USERS=true` (or set `TELEGRAM_ALLOWED_USERS` to your Telegram user ID for restricted access)
7. The gateway will restart automatically — check the status widget in the bottom-right corner

### 4. Test It

Open your new bot in Telegram and send a message. Hermes will respond using the model you configured. You can check the **Sessions** page in the dashboard to see the conversation, token usage, and tool calls.

### 5. Persist Your Data (Recommended)

Attach a Railway volume so your config, sessions, and memories survive redeploys:

1. Right-click the service in your Railway project
2. Select **Attach Volume**
3. Set mount path to `/root/.hermes`

## Authentication

This template uses the upstream Hermes dashboard's own auth gate (the same one that ships with Hermes Agent). The proxy is a near pass-through that:

- Forwards the browser's session cookies to the upstream, so the upstream's gate sees the same session the browser holds.
- Injects `X-Forwarded-Proto: https` and `X-Forwarded-Host` so the upstream can emit hardened `__Host-` / `__Secure-` cookies over the public Railway HTTPS front.
- Renders a custom-branded (teal/dark) landing page that links to the upstream's real provider picker.

A single sign-in covers both the browser dashboard and a remote Hermes Desktop (Settings → Gateway → Remote URL). No second login is required: the WebSocket uses the upstream's `/api/auth/ws-ticket` ticket flow on top of the same session cookie.

### Recommended: Nous Research OAuth (Portal)

OAuth against the Nous Portal is the path the upstream recommends for any deployment reachable beyond a trusted LAN. It delegates identity, MFA and rate-limiting to the Portal, and the dashboard's auth gate never has to know the user's password.

**On the Portal** (one-time):

1. Sign in to your Portal account.
2. Register this dashboard as a new client. Note the issued `client_id` (it has the shape `agent:cmq5z4jsb0011hs0cj1g880b6`).
3. Register the redirect URI as `https://<your-railway-domain>/auth/callback` (must match exactly, no trailing slash, no port).

**On Railway** — set these environment variables:

| Variable | Value | Required? |
|---|---|---|
| `HERMES_DASHBOARD_OAUTH_CLIENT_ID` | The `agent:...` id from the Portal | **yes** |
| `HERMES_DASHBOARD_OAUTH_PORTAL_URL` | `https://portal.nousresearch.com` (default) | optional, override only for staging |
| `HERMES_DASHBOARD_PUBLIC_URL` | `https://<your-railway-domain>` (no trailing slash) | **yes** |

Redeploy. The upstream's auth gate engages on non-loopback binds; if the OAuth client_id is set, the Nous provider registers automatically and the login page shows a **Sign in with Nous Research** button. `/api/status` will return `auth_required: true, auth_providers: ["nous"]`.

### Optional: Username & Password (fallback / local-only)

If you also want a local username+password provider (e.g. as a recovery path, or to share read access with a second operator), set:

| Variable | Value | Notes |
|---|---|---|
| `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` | e.g. `admin` (avoid the default `admin` for internet-facing deploys) | **yes** to enable |
| `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD` | a strong password (≥ 16 chars) | plaintext — Railway stores it as a secret |
| `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH` | a precomputed `scrypt$...` hash (preferred) | alternative to plaintext |
| `HERMES_DASHBOARD_BASIC_AUTH_SECRET` | `$(openssl rand -base64 32)` | session signing key — set to a stable value so sessions survive redeploys |
| `HERMES_DASHBOARD_BASIC_AUTH_TTL_SECONDS` | `43200` (12h, default) | access-token lifetime |

The upstream hashes the password with scrypt, applies a 10/min IP rate limit on `/auth/password-login`, runs a constant-time compare against a dummy hash for unknown usernames, and mints a stateless HMAC session cookie. The username/password provider is documented as **local / trusted-network use only** by the upstream — do not expose a password-protected dashboard to the open internet without OAuth in front of it.

### Migrating from the old `DASHBOARD_USER` / `DASHBOARD_PASSWORD` proxy

If you previously deployed with `DASHBOARD_USER` and `DASHBOARD_PASSWORD` (the old proxy minted its own `hermes_auth` HMAC cookie):

1. The old `DASHBOARD_USER` / `DASHBOARD_PASSWORD` env vars still work — the upstream reads them through its basic-auth provider via `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD` once you set those. The old proxy cookie is **not** honoured by the new proxy; users will be asked to sign in once on the first visit to the new deploy.
2. To preserve sessions across redeploys, set `HERMES_DASHBOARD_BASIC_AUTH_SECRET` to a stable value (default behaviour is a random per-process secret that invalidates all sessions on restart).
3. To enable OAuth on top, additionally set `HERMES_DASHBOARD_OAUTH_CLIENT_ID` + `HERMES_DASHBOARD_PUBLIC_URL` from the OAuth section above. Both providers will appear on the login page simultaneously.
4. After redeploy, verify with `curl -sS https://<your-domain>/api/status | jq '.auth_required, .auth_providers'` — it should report `true` and list `nous` (and `basic` if you set the fallback).

### Connecting Hermes Desktop

1. Open the Desktop app → Settings → Gateway.
2. Set **Remote URL** to `https://<your-railway-domain>` (no port — the proxy is on 443 from Railway's side).
3. The app discovers the configured providers and shows a matching **Sign in** button. Click it, complete the OAuth flow in the browser, return to the app.
4. Sessions refresh automatically. As long as `HERMES_DASHBOARD_BASIC_AUTH_SECRET` (for the password provider) is set to a stable value, you stay signed in across restarts.

## File Browser (Embedded)

This deployment includes **File Browser** running inside the same container, giving you direct web access to your agent's files without needing a separate service.

### Accessing File Browser

1. In your Railway dashboard, click on your service
2. Go to the **Settings** tab
3. Under **Networking**, add a new public domain/port mapping for port **8080**
4. Railway will generate a URL like `https://your-service-8080.up.railway.app`
5. Open that URL to access the File Browser UI

### What You Can Do

- Browse, upload, download, and manage files in `/app/data`
- View and edit text files directly in the browser
- Create folders and organize your agent's outputs
- The File Browser shares the same filesystem as the agent

### Volume Mount for File Browser

If you want File Browser's files to persist across redeploys, you can either:

- **Option A (Recommended):** Mount the same volume at `/app/data` — this gives you a persistent file storage accessible by both the agent and File Browser
- **Option B:** Use the existing `/root/.hermes` volume mount — agent files (config, sessions, skills) persist there, and you can access them via the agent's tools

### File Browser Authentication

File Browser supports optional authentication via environment variable:

| Modo | Como configurar |
|------|-----------------|
| **Sem senha** (padrão) | Não defina `FILEBROWSER_PASSWORD` — acesso livre com `--noauth` |
| **Com senha** | Defina `FILEBROWSER_PASSWORD` no Railway → login obrigatório (user: `admin`) |

Para ativar a senha, vá em **Variables** no Railway e adicione:
- `FILEBROWSER_PASSWORD` = sua senha escolhida

O File Browser reinicia automaticamente com autenticação habilitada.

## Common Use Cases

- Run a personal AI assistant on Telegram, Discord, or Slack with persistent memory and tool use
- Manage your agent from any browser — configure models, API keys, sessions, and analytics through the dashboard
- Schedule recurring AI tasks with cron jobs and monitor usage and costs
- Access and manage your agent's files directly through the built-in File Browser

## Dependencies

- At least one LLM provider API key ([OpenRouter](https://openrouter.ai/), [Anthropic](https://console.anthropic.com/), [OpenAI](https://platform.openai.com/), or [DeepSeek](https://platform.deepseek.com/))
- A Telegram, Discord, or Slack bot token (if using messaging platforms)

### Deployment Dependencies

- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs)
- [Web Dashboard Guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard)
- [Telegram BotFather](https://t.me/BotFather) (for Telegram setup)
- [OpenRouter](https://openrouter.ai/) (recommended LLM provider)

## Why Use Railway?

Railway is a singular platform to deploy your infrastructure stack. Railway will host your infrastructure so you don't have to deal with configuration, while allowing you to vertically and horizontally scale it.
