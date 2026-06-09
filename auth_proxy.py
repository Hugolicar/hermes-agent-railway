#!/usr/bin/env python3
"""Thin auth-aware proxy for the Hermes dashboard on Railway.

This proxy is a near pass-through plus a custom-branded login page. It
delegates authentication to the upstream Hermes dashboard (basic + Nous
OAuth providers) so a single sign-in covers browser + Hermes Desktop
``/api/ws``.

The proxy intentionally does **not** maintain its own credential store or
session cookie. Every login, session and WebSocket ticket is handled by
the upstream. The proxy's job is to:

  1. Render a custom-branded login page (teal/dark) that links to the
     upstream's provider-selection flow.
  2. Forward the browser's session cookies to the upstream on every HTTP
     request and WebSocket upgrade, so the upstream's own auth gate sees
     the same session the browser holds.
  3. Inject ``X-Forwarded-Proto: https`` and ``X-Forwarded-Host`` so the
     upstream can emit ``Secure`` cookies with the right ``__Host-`` /
     ``__Secure-`` prefix. Without these hints the upstream thinks the
     request arrived in plain HTTP and refuses to set the cookies the
     browser will actually accept over HTTPS.
  4. Surface the Railway gateway-status widget in proxied HTML pages.
"""

import asyncio
import os
import string
import subprocess
import sys

from aiohttp import web, ClientSession, ClientTimeout, WSMsgType

HERMES_HOME = "/root/.hermes"
UPSTREAM = "http://127.0.0.1:9119"
# Public scheme/host the upstream is expected to be reached under. Used to
# populate X-Forwarded-Proto and X-Forwarded-Host so the upstream can
# decide the right cookie hardening (Secure flag, __Host- prefix).
PUBLIC_SCHEME = os.environ.get("HERMES_DASHBOARD_PUBLIC_SCHEME", "https").strip() or "https"
PUBLIC_HOST = os.environ.get("HERMES_DASHBOARD_PUBLIC_HOST", "").strip()

# -- Branded login page (teal/dark, matches user's skin) -----------------
# This page is served as a *landing* UI; clicking "Continue" sends the
# user to the upstream's /login which lists the actually-configured
# providers (OAuth and/or username/password). We don't re-implement the
# provider picker here because the upstream is the source of truth for
# which providers are registered -- doing it here would drift.
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
  @keyframes fadeUp { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes lineGrow { from { transform: scaleX(0); } to { transform: scaleX(1); } }
  .login-wrapper { position: relative; z-index: 1; width: 100%; max-width: 400px; padding: 0 1.5rem; animation: fadeUp 0.8s cubic-bezier(0.16, 1, 0.3, 1) both; }
  .brand { text-align: center; margin-bottom: 3rem; }
  .brand-icon { width: 36px; height: 36px; margin: 0 auto 1.2rem; border: 1.5px solid var(--accent); border-radius: 10px; display: flex; align-items: center; justify-content: center; color: var(--accent); font-family: 'Cormorant Garamond', serif; font-size: 1.1rem; font-weight: 600; opacity: 0; animation: fadeUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.1s both; }
  .brand h1 { font-family: 'Cormorant Garamond', serif; font-weight: 400; font-size: 1.6rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text); opacity: 0; animation: fadeUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.2s both; }
  .brand p { font-size: 0.78rem; color: var(--text-muted); margin-top: 0.5rem; letter-spacing: 0.04em; opacity: 0; animation: fadeUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.3s both; }
  .divider { height: 1px; background: linear-gradient(90deg, transparent, var(--border-focus), transparent); margin-bottom: 2.5rem; transform-origin: center; animation: lineGrow 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.4s both; }
  .card { opacity: 0; animation: fadeUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.45s both; }
  .error { background: var(--error-bg); border: 1px solid var(--error-border); color: var(--error-text); padding: 0.6rem 0.9rem; border-radius: 8px; font-size: 0.8rem; margin-bottom: 1.25rem; text-align: center; }
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
    <p class="meta">Sign-in is handled by the configured providers (Nous Research, username &amp; password, or both).</p>
  </div>
</div>
</body>
</html>
"""


async def login_page(request):
    """Custom-branded login landing.

    Renders the teal/dark page. Clicking the CTA navigates to
    ``/upstream-login`` which in turn proxies to the upstream's real
    ``/login`` (provider picker rendered server-side by the upstream).
    """
    error = ""
    if request.query.get("error"):
        error = '<div class="error">Sign-in failed. Please try again.</div>'
    return web.Response(
        text=string.Template(LOGIN_HTML).safe_substitute(error=error),
        content_type="text/html",
    )


async def upstream_login(request):
    """Redirect the browser to the upstream's /login page.

    The upstream is the source of truth for which providers are
    registered, so we let it render the provider picker. We follow the
    upstream's redirect (if any) and forward the final HTML back to the
    browser untouched, except for the standard gateway-widget injection.

    NOTE: as of the ``fix/disable-nous-provider-ui`` branch, we also
    strip the Nous Research OAuth button from the picker HTML. The
    Nous provider is still registered upstream and the PKCE/cookie
    samesite patch is in place, but the Portal round-trip is failing
    on the callback in this environment (root cause still under
    investigation). Until that is resolved the basic-auth provider
    is the only working login path, so we hide the broken button
    to avoid confusing users. Revert this block + the
    ``_nous_blocked`` handler in :func:`proxy` to re-enable Nous.
    """
    async with ClientSession() as session:
        url = f"{UPSTREAM}/login"
        if request.query_string:
            url = f"{url}?{request.query_string}"
        # Same X-Forwarded-* hints as the main proxy path -- the
        # upstream's /login page emits session cookies whose
        # Secure/prefix depends on the request scheme it observes.
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
                    # Strip the Nous OAuth button. The upstream renders
                    # the provider picker as a flat list of
                    # <a class="provider-btn" href="/auth/login?provider=...">
                    # anchors; we remove the Nous one plus any trailing
                    # whitespace/newline so the layout still looks clean
                    # with only the basic-auth form remaining.
                    html = _strip_provider_button(html, "nous")
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


import re as _re

_NOUS_BTN_RE = _re.compile(
    r'<a\b[^>]*class="provider-btn"[^>]*href="[^"]*provider=nous[^"]*"[^>]*>.*?</a>\s*',
    _re.DOTALL | _re.IGNORECASE,
)


def _strip_provider_button(html: str, provider_name: str) -> str:
    """Remove a provider's button anchor from an upstream login page.

    The upstream emits a flat list of provider buttons; we use a
    conservative regex that matches the exact anchor shape used in
    the picker (one anchor per provider, no nested anchors). If the
    shape ever changes upstream the regex will silently no-op,
    which is the safer failure mode (the broken button reappears
    but the basic flow still works).
    """
    if provider_name == "nous":
        return _NOUS_BTN_RE.sub("", html)
    return html


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
    # uses prefix variants (__Host-, __Secure-, bare) and the names
    # hermes_session_at/hermes_session_rt, so cover all of them.
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


# -- Health check (used by Railway) --------------------------------------

async def health(request):
    # Also check upstream reachability
    try:
        async with ClientSession() as session:
            async with session.get(f"{UPSTREAM}/api/health", timeout=ClientTimeout(total=2)) as r:
                upstream_ok = r.status == 200
    except Exception:
        upstream_ok = False
    return web.json_response({"status": "ok", "upstream_ok": upstream_ok})


# -- Pass-through middleware: no auth gating here. Upstream handles it. --

@web.middleware
async def pass_through_middleware(request, handler):
    return await handler(request)


# -- Gateway widget injection into HTML responses ------------------------

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
    """
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "transfer-encoding")
    }
    headers["X-Forwarded-Proto"] = PUBLIC_SCHEME
    if PUBLIC_HOST:
        headers["X-Forwarded-Host"] = PUBLIC_HOST
    return headers


async def proxy_ws(request):
    """WebSocket pass-through with cookie forwarding.

    The upstream's ``/api/ws`` rejects unauthenticated upgrades with
    close code 4401 ("WS ticket didn't authenticate") in gated mode.
    The Desktop client authenticates by first calling
    ``POST /api/auth/ws-ticket`` (which uses the session cookies) and
    then connecting with ``?ticket=...`` -- so the cookies don't have to
    ride on the WS upgrade itself. But forwarding them anyway is
    harmless and keeps the proxy uniform with the HTTP path; a future
    ``?internal=`` credential path the upstream might add would also
    benefit from session-aware routing.
    """
    ws_client = web.WebSocketResponse()
    await ws_client.prepare(request)

    async with ClientSession() as session:
        url = f"ws://127.0.0.1:9119{request.path_qs}"
        upstream_headers = _upstream_headers(request)
        cookies_header = "; ".join(f"{k}={v}" for k, v in request.cookies.items())
        if cookies_header:
            upstream_headers["Cookie"] = cookies_header
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
    """HTTP pass-through with cookie + forwarded-headers forwarding.

    The Nous OAuth provider is currently disabled in this deploy (see
    :func:`upstream_login` for the rationale). Any direct request to
    ``/auth/login?provider=nous`` is short-circuited to a 404 so
    bookmarked links or stale tabs don't 302 the user into a broken
    Portal round-trip.
    """
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await proxy_ws(request)

    # Short-circuit Nous login attempts. We match on the query string
    # instead of the path because the upstream route is ``/auth/login``
    # (no provider segment) and the provider is selected via ``?provider=``.
    if request.method == "GET" and request.path == "/auth/login" and \
       request.query.get("provider", "").lower() == "nous":
        return web.Response(
            status=404,
            text="Nous Research OAuth login is temporarily disabled in this "
                 "deploy. Use the Username & Password form on the login page.",
            content_type="text/plain",
        )

    async with ClientSession() as session:
        url = f"{UPSTREAM}{request.path_qs}"
        headers = _upstream_headers(request)
        # Forward the browser's session cookies. The upstream is the auth
        # gate; without this forward it would never see the session cookie
        # the user just established via /auth/password-login or
        # /auth/callback, and every subsequent request would 302 back to
        # /login in an infinite loop.
        if request.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in request.cookies.items())

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
    # Note: no auth_middleware. The upstream dashboard runs its own auth
    # gate (gated mode engages on non-loopback binds). The proxy is
    # transparent: it forwards the browser's session cookies, sets
    # X-Forwarded-Proto so the upstream emits Secure cookies, and lets
    # the upstream decide.
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
