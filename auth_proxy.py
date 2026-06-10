#!/usr/bin/env python3
"""Auth-aware delegate proxy for Hermes dashboard on Railway.

DELEGATE PATTERN (jun 2026): this proxy does NOT validate credentials
locally and does NOT mint its own session cookie. It is a thin
pass-through to the upstream dashboard (:9119), which runs the basic
auth provider configured via HERMES_DASHBOARD_BASIC_AUTH_* env vars.
The proxy's only job is:

  1. Serve a branded landing page at /login.
  2. Render a "Continue to sign in" link that points to /upstream-login,
     which GETs the upstream's actual /login (the real provider picker).
  3. Forward the browser's session cookies to the upstream on every
     HTTP and WebSocket request (the upstream is the auth gate).
  4. Inject X-Forwarded-Proto: https + X-Forwarded-Host on every
     proxied request so the upstream's detect_https() mints Secure
     __Host- prefixed cookies that the browser will accept over
     the public HTTPS Railway front.

The upstream is the source of truth for which providers are registered
(basic, nous, or both). The proxy never re-implements the picker or
validates credentials — those were the two bugs in the previous version
that caused a 302 redirect loop on /login and 401s on /api/status.

Reference: hermes-remote-dashboard-connection/templates/auth_proxy_delegate.py
"""

import asyncio
import os
import string
import subprocess
import sys

from aiohttp import web, ClientSession, ClientTimeout, WSMsgType

HERMES_HOME = "/root/.hermes"
UPSTREAM = "http://127.0.0.1:9119"

# Public scheme/host the upstream is reached under. Used to populate
# X-Forwarded-Proto and X-Forwarded-Host so the upstream can decide
# the right cookie hardening (Secure flag, __Host- prefix).
PUBLIC_SCHEME = os.environ.get("HERMES_DASHBOARD_PUBLIC_SCHEME", "https").strip() or "https"
PUBLIC_HOST = os.environ.get("HERMES_DASHBOARD_PUBLIC_HOST", "hugoloc.click").strip()


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes Agent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600&family=DM+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0a0f14;
    --surface: #111920;
    --border: rgba(255,255,255,0.06);
    --border-focus: rgba(45,212,191,0.4);
    --text: #e0f0f0;
    --text-muted: #7899aa;
    --accent: #2dd4bf;
    --accent-dim: rgba(45,212,191,0.1);
    --error-bg: rgba(180,60,60,0.1);
    --error-border: rgba(180,60,60,0.25);
    --error-text: #d4908a;
  }
  body {
    font-family: 'DM Sans', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    overflow: hidden;
  }
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background:
      radial-gradient(ellipse 80% 60% at 50% 0%, rgba(45,212,191,0.04) 0%, transparent 60%),
      radial-gradient(ellipse 60% 80% at 80% 100%, rgba(255,255,255,0.02) 0%, transparent 50%);
    pointer-events: none;
  }
  body::after {
    content: '';
    position: fixed;
    inset: 0;
    background: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
    pointer-events: none;
    opacity: 0.5;
  }
  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(16px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes lineGrow {
    from { transform: scaleX(0); }
    to { transform: scaleX(1); }
  }
  .login-wrapper {
    position: relative;
    z-index: 1;
    width: 100%;
    max-width: 400px;
    padding: 0 1.5rem;
    animation: fadeUp 0.8s cubic-bezier(0.16, 1, 0.3, 1) both;
  }
  .brand {
    text-align: center;
    margin-bottom: 3rem;
  }
  .brand-icon {
    width: 36px;
    height: 36px;
    margin: 0 auto 1.2rem;
    border: 1.5px solid var(--accent);
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--accent);
    font-family: 'Cormorant Garamond', serif;
    font-size: 1.1rem;
    font-weight: 600;
    opacity: 0;
    animation: fadeUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.1s both;
  }
  .brand h1 {
    font-family: 'Cormorant Garamond', serif;
    font-weight: 400;
    font-size: 1.6rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--text);
    opacity: 0;
    animation: fadeUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.2s both;
  }
  .brand p {
    font-size: 0.78rem;
    color: var(--text-muted);
    margin-top: 0.5rem;
    letter-spacing: 0.04em;
    opacity: 0;
    animation: fadeUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.3s both;
  }
  .divider {
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--border-focus), transparent);
    margin-bottom: 2.5rem;
    transform-origin: center;
    animation: lineGrow 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.4s both;
  }
  .card {
    opacity: 0;
    animation: fadeUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.45s both;
  }
  .field {
    margin-bottom: 1.25rem;
  }
  label {
    display: block;
    font-size: 0.7rem;
    font-weight: 500;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 0.5rem;
  }
  input {
    width: 100%;
    padding: 0.75rem 1rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    font-size: 0.9rem;
    outline: none;
    transition: border-color 0.3s, box-shadow 0.3s;
  }
  input::placeholder { color: var(--text-muted); opacity: 0.5; }
  input:focus {
    border-color: var(--border-focus);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }
  button {
    width: 100%;
    padding: 0.8rem;
    margin-top: 0.5rem;
    background: var(--accent);
    color: var(--bg);
    border: none;
    border-radius: 8px;
    font-family: 'DM Sans', sans-serif;
    font-size: 0.85rem;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    cursor: pointer;
    transition: transform 0.2s, opacity 0.2s;
  }
  button:hover { opacity: 0.88; transform: translateY(-1px); }
  button:active { transform: translateY(0); }
  .error {
    background: var(--error-bg);
    border: 1px solid var(--error-border);
    color: var(--error-text);
    padding: 0.6rem 0.9rem;
    border-radius: 8px;
    font-size: 0.8rem;
    margin-bottom: 1.25rem;
    text-align: center;
  }
  a.continue {
    display: block; text-align: center; text-decoration: none;
    width: 100%; padding: 0.8rem; margin-top: 0.5rem;
    background: var(--accent); color: var(--bg); border: none; border-radius: 8px;
    font-family: 'DM Sans', sans-serif; font-size: 0.85rem; font-weight: 500;
    letter-spacing: 0.06em; text-transform: uppercase; cursor: pointer;
    transition: transform 0.2s, opacity 0.2s;
  }
  a.continue:hover { opacity: 0.88; transform: translateY(-1px); }
  a.continue:active { transform: translateY(0); }
  .meta { margin-top: 1.5rem; font-size: 0.7rem; color: var(--text-muted); text-align: center; letter-spacing: 0.04em; }
</style>
</head>
<body>
<div class="login-wrapper">
  <div class="brand">
    <div class="brand-icon">H</div>
    <h1>Hermes</h1>
    <p>Agent Console</p>
  </div>
  <div class="divider"></div>
  <div class="card">
    $error
    <a class="continue" href="/upstream-login">Continue to sign in</a>
    <p class="meta">Sign-in is handled by the configured providers (basic auth, Nous Research, or both).</p>
  </div>
</div>
</body>
</html>"""


async def login_page(request):
    """Custom-branded login landing.

    Renders a 'Continue to sign in' link to /upstream-login, which
    proxies to the upstream's real /login page where the actual
    provider picker (basic, nous, or both) lives.
    """
    error = ""
    if request.query.get("error"):
        error = '<div class="error">Sign-in failed. Please try again.</div>'
    return web.Response(
        text=string.Template(LOGIN_HTML).safe_substitute(error=error),
        content_type="text/html",
    )


async def upstream_login(request):
    """Pass-through to the upstream's /login page.

    The upstream is the source of truth for which providers are
    registered, so we let it render the provider picker. We forward
    X-Forwarded-Proto and X-Forwarded-Host so the upstream emits
    session cookies whose Secure/__Host- prefix depends on the
    public request scheme, not the internal loopback scheme.
    """
    async with ClientSession() as session:
        url = f"{UPSTREAM}/login"
        if request.query_string:
            url = f"{url}?{request.query_string}"
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "transfer-encoding")
        }
        headers["X-Forwarded-Proto"] = PUBLIC_SCHEME
        if PUBLIC_HOST:
            headers["X-Forwarded-Host"] = PUBLIC_HOST
        try:
            async with session.get(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=ClientTimeout(total=10),
            ) as resp:
                content = await resp.read()
                excluded = {"transfer-encoding", "content-encoding", "content-length"}
                proxy_headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}
                content_type = resp.headers.get("content-type", "")
                if "text/html" in content_type:
                    html = content.decode("utf-8", errors="replace")
                    html = html.replace("</body>", GATEWAY_WIDGET + "</body>")
                    return web.Response(
                        status=resp.status,
                        headers={k: v for k, v in proxy_headers.items() if k.lower() != "content-type"},
                        text=html,
                        content_type="text/html",
                    )
                return web.Response(status=resp.status, headers=proxy_headers, body=content)
        except Exception as e:
            print(f"[auth_proxy] upstream /login fetch failed: {e}", file=sys.stderr)
            raise web.HTTPFound("/login?error=1")


async def logout(request):
    """Forward logout to upstream, then bounce the browser back to /login."""
    async with ClientSession() as session:
        try:
            cookies_header = "; ".join(f"{k}={v}" for k, v in request.cookies.items())
            headers = {}
            if cookies_header:
                headers["Cookie"] = cookies_header
            headers["X-Forwarded-Proto"] = PUBLIC_SCHEME
            if PUBLIC_HOST:
                headers["X-Forwarded-Host"] = PUBLIC_HOST
            await session.post(
                f"{UPSTREAM}/auth/logout",
                headers=headers,
                cookies={k: v for k, v in request.cookies.items()},
                allow_redirects=False,
                timeout=ClientTimeout(total=5),
            )
        except Exception:
            pass
    resp = web.HTTPFound("/login")
    # Best-effort clear every plausible session cookie name. The upstream
    # uses prefix variants (__Host-, __Secure-, bare).
    for name in (
        "hermes_session_at",
        "hermes_session_rt",
        "hermes_session_pkce",
        "__Host-hermes_session_at",
        "__Host-hermes_session_rt",
        "__Secure-hermes_session_at",
        "__Secure-hermes_session_rt",
    ):
        resp.del_cookie(name, path="/")
    return resp


# -- Pass-through middleware: no auth gating here. Upstream handles it. --

@web.middleware
async def pass_through_middleware(request, handler):
    return await handler(request)


gateway_process = None


def start_gateway():
    global gateway_process
    if gateway_process and gateway_process.poll() is None:
        gateway_process.terminate()
        try:
            gateway_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            gateway_process.kill()
    gateway_process = subprocess.Popen(["hermes", "gateway", "run"])


RESTART_PATHS = {
    ("PUT", "/api/config"),
    ("PUT", "/api/env"),
    ("DELETE", "/api/env"),
}


def volume_attached():
    return os.path.ismount(HERMES_HOME)


async def restart_gateway(request):
    start_gateway()
    return web.json_response({"status": "gateway restarted"})


async def gateway_status(request):
    running = gateway_process is not None and gateway_process.poll() is None
    return web.json_response({
        "running": running,
        "volume": volume_attached(),
    })


GATEWAY_WIDGET = """
<div id="gw-widget" style="position:fixed;bottom:20px;right:20px;z-index:99999;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px;">
  <div style="background:#111920;border:1px solid rgba(45,212,191,0.2);border-radius:10px;
    padding:12px 16px;display:flex;flex-direction:column;gap:8px;
    box-shadow:0 4px 20px rgba(0,0,0,0.4);min-width:180px;">
    <div style="display:flex;align-items:center;gap:10px;">
      <span id="gw-dot" style="width:8px;height:8px;border-radius:50%;background:#888;flex-shrink:0;"></span>
      <span id="gw-label" style="color:#7899aa;flex:1;">Gateway</span>
      <button id="gw-btn" onclick="gwRestart()" style="background:#2dd4bf;color:#0a0f14;border:none;
        border-radius:5px;padding:4px 12px;font-size:12px;font-weight:600;cursor:pointer;">Restart</button>
    </div>
    <div id="gw-vol" style="display:none;font-size:11px;padding-top:4px;border-top:1px solid rgba(45,212,191,0.1);"></div>
  </div>
</div>
<script>
function gwStatus(){
  fetch('/api/gateway/status').then(r=>r.json()).then(d=>{
    document.getElementById('gw-dot').style.background=d.running?'#4ade80':'#ef4444';
    document.getElementById('gw-label').textContent=d.running?'Gateway running':'Gateway stopped';
    var vol=document.getElementById('gw-vol');
    vol.style.display='block';
    if(d.volume){
      vol.innerHTML='<span style="color:#4ade80;">&#x2713;</span> <span style="color:#7899aa;">Volume attached</span>';
    }else{
      vol.innerHTML='<span style="color:#fbbf24;">&#x26A0;</span> <span style="color:#fbbf24;">No volume \u2014 data will not persist</span>';
    }
  }).catch(()=>{});
}
function gwRestart(){
  var b=document.getElementById('gw-btn');b.textContent='Restarting...';b.disabled=true;
  fetch('/api/gateway/restart',{method:'POST'}).then(()=>{
    setTimeout(()=>{b.textContent='Restart';b.disabled=false;gwStatus();},3000);
  }).catch(()=>{b.textContent='Restart';b.disabled=false;});
}
gwStatus();setInterval(gwStatus,10000);
</script>
"""


async def health(request):
    return web.json_response({"status": "ok"})


def _upstream_headers(request):
    """Build the header dict for an upstream request.

    Drops ``Host`` (would be the proxy's 127.0.0.1) and
    ``Transfer-Encoding`` (let aiohttp re-add it). Adds:

    * ``X-Forwarded-Proto`` -- the upstream needs this to decide whether
      to set the ``Secure`` cookie flag. Without it the upstream thinks
      the request is plain HTTP and refuses to set Secure cookies the
      browser will accept over the public HTTPS front.
    * ``X-Forwarded-Host`` -- mirrors the public origin so PKCE redirect
      URIs and cookie ``Domain`` attributes resolve correctly.
    * ``Cookie`` -- the browser's session cookies. The upstream is the
      auth gate; without this forward it would never see the session
      the user just established and would 401 in a loop.
    """
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "transfer-encoding")
    }
    headers["X-Forwarded-Proto"] = PUBLIC_SCHEME
    if PUBLIC_HOST:
        headers["X-Forwarded-Host"] = PUBLIC_HOST
    if request.cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in request.cookies.items())
    return headers


async def proxy_ws(request):
    """WebSocket pass-through with cookie forwarding.

    The upstream's /api/ws rejects unauthenticated upgrades with close
    code 4401 in gated mode. The Desktop client authenticates by first
    calling POST /api/auth/ws-ticket (which uses the session cookies) and
    then connecting with ?ticket=... — so the cookies don't strictly
    have to ride on the WS upgrade itself. But forwarding them is
    harmless and keeps the proxy uniform with the HTTP path.
    """
    ws_client = web.WebSocketResponse()
    await ws_client.prepare(request)

    async with ClientSession() as session:
        url = f"ws://127.0.0.1:9119{request.path_qs}"
        upstream_headers = _upstream_headers(request)
        async with session.ws_connect(url, headers=upstream_headers) as ws_upstream:

            async def forward(src, dst):
                async for msg in src:
                    if msg.type == WSMsgType.TEXT:
                        await dst.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break

            await asyncio.gather(
                forward(ws_client, ws_upstream),
                forward(ws_upstream, ws_client),
            )

    return ws_client


async def proxy(request):
    """HTTP pass-through with cookie + forwarded-headers forwarding."""
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await proxy_ws(request)

    async with ClientSession() as session:
        url = f"{UPSTREAM}{request.path_qs}"
        headers = _upstream_headers(request)

        body = await request.read()
        async with session.request(
            request.method,
            url,
            headers=headers,
            data=body,
            cookies=dict(request.cookies),
            allow_redirects=False,
        ) as resp:
            excluded = {"transfer-encoding", "content-encoding", "content-length"}
            proxy_headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}
            content = await resp.read()
            if (request.method, request.path) in RESTART_PATHS and resp.status < 400:
                start_gateway()

            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type:
                html_headers = {k: v for k, v in proxy_headers.items() if k.lower() != "content-type"}
                html = content.decode("utf-8", errors="replace")
                html = html.replace("</body>", GATEWAY_WIDGET + "</body>")
                return web.Response(status=resp.status, headers=html_headers, text=html, content_type="text/html")
            return web.Response(status=resp.status, headers=proxy_headers, body=content)


async def on_startup(app):
    start_gateway()


def create_app():
    # No auth_middleware. The upstream dashboard runs its own auth
    # gate (gated mode engages on non-loopback binds). The proxy is
    # transparent: it forwards the browser's session cookies, sets
    # X-Forwarded-Proto so the upstream emits Secure cookies, and
    # lets the upstream decide.
    app = web.Application(middlewares=[pass_through_middleware])
    app.on_startup.append(on_startup)
    app.router.add_get("/login", login_page)
    app.router.add_get("/upstream-login", upstream_login)
    app.router.add_get("/logout", logout)
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/gateway/restart", restart_gateway)
    app.router.add_get("/api/gateway/status", gateway_status)
    app.router.add_route("*", "/{path_info:.*}", proxy)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    web.run_app(create_app(), host="0.0.0.0", port=port)
