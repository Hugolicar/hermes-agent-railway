#!/usr/bin/env python3
"""Regression test: auth_proxy must preserve repeated Set-Cookie headers."""

import asyncio
import importlib.util
import sys
from pathlib import Path

from aiohttp import ClientSession, web

ROOT = Path(__file__).resolve().parents[1]
PROXY_PATH = ROOT / "auth_proxy.py"


def load_proxy():
    spec = importlib.util.spec_from_file_location("auth_proxy_under_test", PROXY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def main() -> None:
    upstream = web.Application()

    async def password_login(_request):
        response = web.json_response({"ok": True, "next": "/"})
        response.headers.add(
            "Set-Cookie",
            "__Host-hermes_session_at=access; HttpOnly; Path=/; SameSite=Lax; Secure",
        )
        response.headers.add(
            "Set-Cookie",
            "__Host-hermes_session_rt=refresh; HttpOnly; Path=/; SameSite=Lax; Secure",
        )
        response.headers.add(
            "Set-Cookie",
            "__Host-hermes_session_provider=basic; HttpOnly; Path=/; SameSite=Lax; Secure",
        )
        return response

    upstream.router.add_post("/auth/password-login", password_login)
    upstream_runner = web.AppRunner(upstream)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    upstream_port = upstream_site._server.sockets[0].getsockname()[1]

    proxy_module = load_proxy()
    proxy_module.UPSTREAM = f"http://127.0.0.1:{upstream_port}"
    proxy_module.start_gateway = lambda: None
    proxy_app = proxy_module.create_app()
    proxy_app.on_startup.clear()
    proxy_runner = web.AppRunner(proxy_app)
    await proxy_runner.setup()
    proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
    await proxy_site.start()
    proxy_port = proxy_site._server.sockets[0].getsockname()[1]

    try:
        async with ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{proxy_port}/auth/password-login",
                json={
                    "provider": "basic",
                    "username": "user",
                    "password": "pass",
                    "next": "/",
                },
                allow_redirects=False,
            ) as response:
                set_cookies = response.headers.getall("Set-Cookie", [])
                print(f"received Set-Cookie headers: {len(set_cookies)}")
                for cookie in set_cookies:
                    print(f"  {cookie}")
                expected = {
                    "__Host-hermes_session_at",
                    "__Host-hermes_session_rt",
                    "__Host-hermes_session_provider",
                }
                actual = {cookie.split("=", 1)[0] for cookie in set_cookies}
                if actual != expected:
                    raise AssertionError(
                        f"proxy collapsed/lost repeated Set-Cookie headers: "
                        f"expected={sorted(expected)}, actual={sorted(actual)}"
                    )
                if response.status != 200:
                    raise AssertionError(f"expected HTTP 200, got {response.status}")
    finally:
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()

    print("PASS: repeated Set-Cookie headers survived the proxy")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise
