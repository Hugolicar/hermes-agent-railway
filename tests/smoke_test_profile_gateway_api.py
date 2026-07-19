#!/usr/bin/env python3
"""HTTP smoke test for authenticated per-profile gateway actions."""

import asyncio
import importlib.util
import sys
from pathlib import Path

from aiohttp import ClientSession, web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class FakeController:
    def __init__(self):
        self.profile = "rafapessoal"
        self.calls = []

    def reconcile(self):
        return self.status()

    def status(self):
        return {
            "profile": "rafapessoal",
            "desired": "stopped",
            "running": False,
            "pid": None,
            "last_error": None,
        }

    def start(self):
        self.calls.append("start")
        result = self.status()
        result.update(desired="running", running=True, pid=4321)
        return result

    def stop(self):
        self.calls.append("stop")
        return self.status()

    def restart(self):
        self.calls.append("restart")
        return self.start()


async def main():
    upstream = web.Application()

    async def protected_profiles(request):
        if request.cookies.get("session") != "valid":
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"profiles": ["default", "rafapessoal"]})

    upstream.router.add_get("/api/profiles", protected_profiles)
    upstream_runner = web.AppRunner(upstream)
    await upstream_runner.setup()
    upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
    await upstream_site.start()
    upstream_port = upstream_site._server.sockets[0].getsockname()[1]

    spec = importlib.util.spec_from_file_location("auth_proxy_profile_test", ROOT / "auth_proxy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.UPSTREAM = f"http://127.0.0.1:{upstream_port}"
    module.start_gateway = lambda: None
    fake = FakeController()
    module.PROFILE_GATEWAY_CONTROLLERS = {"rafapessoal": fake}

    proxy_runner = web.AppRunner(module.create_app())
    await proxy_runner.setup()
    proxy_site = web.TCPSite(proxy_runner, "127.0.0.1", 0)
    await proxy_site.start()
    proxy_port = proxy_site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{proxy_port}"

    try:
        async with ClientSession() as client:
            unauth = await client.post(f"{base}/api/profile-gateways/rafapessoal/start")
            assert unauth.status == 401, await unauth.text()
            assert fake.calls == [], fake.calls

            auth = await client.post(
                f"{base}/api/profile-gateways/rafapessoal/start",
                cookies={"session": "valid"},
            )
            body = await auth.json()
            assert auth.status == 200, body
            assert body["profile"] == "rafapessoal", body
            assert body["running"] is True, body
            assert fake.calls == ["start"], fake.calls

            unknown = await client.post(
                f"{base}/api/profile-gateways/not-allowed/start",
                cookies={"session": "valid"},
            )
            assert unknown.status == 404, await unknown.text()

        print("PASS: authenticated rafapessoal gateway API is session-gated and whitelisted")
    finally:
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
