#!/usr/bin/env python3
"""Static contract test for the injected per-profile gateway controls."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("auth_proxy_widget_test", ROOT / "auth_proxy.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

widget = module.GATEWAY_WIDGET
required = [
    'id="rafa-gw-row"',
    'id="rafa-gw-start"',
    'id="rafa-gw-stop"',
    'id="rafa-gw-restart"',
    "/api/profile-gateways/rafapessoal/status",
    "/api/profile-gateways/rafapessoal/",
    "Rafaela",
]
missing = [marker for marker in required if marker not in widget]
assert not missing, f"missing widget markers: {missing}"
print("PASS: Rafaela Start/Stop/Restart controls are injected into the dashboard widget")
