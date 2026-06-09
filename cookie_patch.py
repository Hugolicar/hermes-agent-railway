"""Patched cookies.py for OAuth cross-site reliability.

Drop-in replacement for the upstream
``hermes_cli.dashboard_auth.cookies`` module, applied at container
startup by ``entrypoint.sh`` (see ``patch_cookies`` there). Every
docstring INSIDE the file is preserved verbatim from upstream — only
the module docstring and the ``SameSite`` literal were changed.

The ONE behavioural change: the ``SameSite`` attribute is no longer
hard-coded to ``lax``. The default stays ``lax`` for backwards
compatibility, but the value can be overridden by setting the env
var ``HERMES_AUTH_COOKIE_SAMESITE`` to ``"none"`` (or ``"strict"``).

Why this exists
---------------
The Portal Nous OAuth flow is a cross-site redirect chain::

    hugoloc.click  ──302──>  portal.nousresearch.com
       (PKCE cookie set with SameSite=Lax)
    portal.nousresearch.com  ──302──>  hugoloc.click/auth/callback
       (browser may NOT send the PKCE cookie back on the
        cross-site return redirect under some privacy-protective
        browsers: Safari ITP, Brave Shields, Firefox ETP-strict,
        some mobile WebViews. With SameSite=None the cookie is
        always sent on top-level cross-site GET.)

Setting ``SameSite=None`` is the spec-compliant fix. RFC 6265bis
requires ``Secure`` when ``SameSite=None``, so when the override is
active the patch also forces ``secure=True`` regardless of the
incoming request scheme (the dashboard is always served over HTTPS
in the Railway deploy, so the cookie is never actually exposed to
plaintext).

Env var contract
----------------
* ``HERMES_AUTH_COOKIE_SAMESITE`` (default ``"lax"``)
    Values: ``"lax"``, ``"strict"``, ``"none"``. Case-insensitive.
    Invalid values fall back to ``"lax"`` and log a warning.

* ``HERMES_AUTH_COOKIE_SAMESITE_DEBUG`` (default ``"0"``)
    When truthy (``1``, ``true``, ``yes``, ``on``), the resolved
    samesite is logged on every cookie set. Off by default to
    avoid log spam.
"""
from __future__ import annotations

import os
import sys
from typing import Optional, Tuple

from fastapi import Request
from fastapi.responses import Response

# Bare cookie names — the request-scoped ``_resolved_name`` helper
# decides whether to prepend ``__Host-`` / ``__Secure-`` based on the
# request's HTTPS + prefix combination.
SESSION_AT_COOKIE = "hermes_session_at"
SESSION_RT_COOKIE = "hermes_session_rt"
PKCE_COOKIE = "hermes_session_pkce"

# Possible name variants we may have to read back. Sorted so most-strict
# wins on iteration when both happen to be present (shouldn't happen in
# practice — a single request emits exactly one variant).
_NAME_VARIANTS = ("__Host-", "__Secure-", "")

# RT cookie Max-Age. Kept at 30 days as a generous upper bound on the cookie's
# browser lifetime; Portal's actual refresh-token TTL (24h, rotating) is the
# real authority — once the RT itself expires/rotates out, a refresh attempt
# returns 400 → RefreshExpiredError → clean re-login, regardless of how long
# the cookie lingers. (Not tightened to 24h here to avoid coupling the cookie
# lifetime to a server-side TTL that can change independently; revisit if the
# stale-cookie refresh churn ever matters.)
_RT_MAX_AGE = 30 * 24 * 60 * 60
_PKCE_MAX_AGE = 10 * 60


# ── PATCH BEGIN ────────────────────────────────────────────────────────
# Resolved at import time. Changing the env var requires restarting
# the dashboard process; supervisord does that automatically when
# the entrypoint rewrites this file.
_SAMESITE_RAW = os.environ.get("HERMES_AUTH_COOKIE_SAMESITE", "lax").strip().lower()
_DEBUG_SAMESITE = os.environ.get("HERMES_AUTH_COOKIE_SAMESITE_DEBUG", "0").strip().lower() in (
    "1", "true", "yes", "on",
)

if _SAMESITE_RAW not in ("lax", "strict", "none"):
    print(
        f"[cookie_patch] HERMES_AUTH_COOKIE_SAMESITE=*** is not a "
        f"valid SameSite value; falling back to 'lax'. Valid: lax|strict|none.",
        file=sys.stderr,
    )
    _SAMESITE = "lax"
else:
    _SAMESITE = _SAMESITE_RAW

print(f"[cookie_patch] dashboard auth cookies: SameSite={_SAMESITE}", file=sys.stderr)
# ── PATCH END ──────────────────────────────────────────────────────────


def _resolved_name(bare: str, *, use_https: bool, prefix: str) -> str:
    """Pick the cookie-prefix variant for the active request shape.

    See module docstring for the prefix selection rules. Mismatch
    between setter and reader would silently break sessions, so this
    function is the single source of truth for naming.
    """
    if not use_https:
        return bare
    if prefix:
        # Path != "/" forbids __Host-; fall back to __Secure-.
        return f"__Secure-{bare}"
    return f"__Host-{bare}"


def _cookie_path(prefix: str) -> str:
    """Cookie ``Path`` attribute for the active deploy shape.

    Under ``X-Forwarded-Prefix: /hermes`` we want ``Path=/hermes`` so:
      a) the browser sends the cookie back on requests under the prefix
         (browsers omit the cookie if request path doesn't start with
         Path);
      b) the cookie doesn't leak to other apps on the same origin
         (``mission-control.tilos.com/billing/...``).

    Direct-deploy (no proxy prefix) gets ``Path=/``.
    """
    return prefix if prefix else "/"


def _common_attrs(*, use_https: bool, prefix: str) -> dict:
    attrs: dict = {
        "httponly": True,
        # PATCH: samesite no longer hard-coded; read from env (default lax).
        "samesite": _SAMESITE,
        "path": _cookie_path(prefix),
    }
    if use_https:
        attrs["secure"] = True
    # PATCH: RFC 6265bis requires Secure when SameSite=None.
    # The Railway deploy is always HTTPS so this never weakens
    # security; it just lets the browser send the PKCE cookie on
    # the cross-site callback redirect.
    if _SAMESITE == "none":
        attrs["secure"] = True
    if _DEBUG_SAMESITE:
        print(f"[cookie_patch] cookie attrs: {attrs}", file=sys.stderr)
    return attrs


def set_session_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    access_token_expires_in: int,
    use_https: bool,
    prefix: str = "",
) -> None:
    """Set the session cookies on the response.

    ``access_token_expires_in`` is in seconds. Use the provider's reported
    TTL for the access token.

    ``refresh_token`` is written as the RT cookie when non-empty. Nous Portal
    issues a 24h rotating refresh token (hermes #37247); a provider that
    omits it returns ``Session.refresh_token == ""`` and we simply don't
    persist the RT cookie — the session then behaves as access-token-only
    until the AT expires. No other branch changes between the two cases.

    ``prefix`` is the normalised X-Forwarded-Prefix value (e.g. ``/hermes``)
    or ``""`` for a direct deploy. It influences both the cookie name
    (``__Host-`` vs ``__Secure-`` vs bare) and the ``Path`` attribute.
    """
    response.set_cookie(
        _resolved_name(SESSION_AT_COOKIE, use_https=use_https, prefix=prefix),
        access_token,
        max_age=access_token_expires_in,
        **_common_attrs(use_https=use_https, prefix=prefix),
    )
    # Contract v1: empty refresh token means "don't persist RT cookie".
    # Keeping a literal empty-value cookie around would be dead state at
    # best, attack surface at worst.
    if refresh_token:
        response.set_cookie(
            _resolved_name(SESSION_RT_COOKIE, use_https=use_https, prefix=prefix),
            refresh_token,
            max_age=_RT_MAX_AGE,
            **_common_attrs(use_https=use_https, prefix=prefix),
        )


def clear_session_cookies(response: Response, *, prefix: str = "") -> None:
    """Emit Max-Age=0 deletions for both session cookies.

    To delete a cookie reliably the deletion's ``Path`` must match the
    set path AND the cookie name must match the variant the setter used.
    We don't know which variant was originally set (cookie prefix
    depends on the request that set it), so we emit deletions for every
    plausible variant under the active path.
    """
    path = _cookie_path(prefix)
    for variant in _NAME_VARIANTS:
        response.set_cookie(
            f"{variant}{SESSION_AT_COOKIE}", "", max_age=0,
            path=path, httponly=True, samesite=_SAMESITE,
  # PATCH: was hard-coded "lax"
        )
        response.set_cookie(
            f"{variant}{SESSION_RT_COOKIE}", "", max_age=0,
            path=path, httponly=True, samesite=_SAMESITE,
  # PATCH: was hard-coded "lax"
        )


def set_pkce_cookie(
    response: Response, *, payload: str, use_https: bool, prefix: str = "",
) -> None:
    response.set_cookie(
        _resolved_name(PKCE_COOKIE, use_https=use_https, prefix=prefix),
        payload,
        max_age=_PKCE_MAX_AGE,
        **_common_attrs(use_https=use_https, prefix=prefix),
    )


def clear_pkce_cookie(response: Response, *, prefix: str = "") -> None:
    path = _cookie_path(prefix)
    for variant in _NAME_VARIANTS:
        response.set_cookie(
            f"{variant}{PKCE_COOKIE}", "", max_age=0,
            path=path, httponly=True, samesite=_SAMESITE,
  # PATCH: was hard-coded "lax"
        )


def _read_with_fallback(
    request: Request, bare_name: str,
) -> Optional[str]:
    """Read a cookie by checking every prefix variant in order.

    The setter chooses one variant based on the active request shape;
    the reader doesn't know which one fired (the request that READS
    the cookie may not be the same shape as the request that SET it
    in pathological cases). Trying all three guarantees we find it.
    """
    for variant in _NAME_VARIANTS:
        value = request.cookies.get(f"{variant}{bare_name}")
        if value is not None:
            return value
    return None


def read_session_cookies(request: Request) -> Tuple[Optional[str], Optional[str]]:
    """Returns (access_token, refresh_token), either may be None."""
    at = _read_with_fallback(request, SESSION_AT_COOKIE)
    rt = _read_with_fallback(request, SESSION_RT_COOKIE)
    return at, rt


def read_pkce_cookie(request: Request) -> Optional[str]:
    return _read_with_fallback(request, PKCE_COOKIE)


def detect_https(request: Request) -> bool:
    """Decide whether to set the ``Secure`` cookie flag.

    Reads ``request.url.scheme`` — under uvicorn's ``proxy_headers=True``
    (which start_server enables when the gate is active), this honours
    ``X-Forwarded-Proto`` from Fly's TLS terminator. Loopback traffic is
    always HTTP so this returns False there.
    """
    return request.url.scheme == "https"
