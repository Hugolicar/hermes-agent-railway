#!/usr/bin/env python3
"""Run every standalone repository test with the current interpreter."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
EXCLUDED = {"run_all.py"}
files = sorted(
    path for path in TESTS.glob("*.py")
    if path.name not in EXCLUDED and not path.name.startswith("__")
)

for path in files:
    print(f"\n==> {path.name}", flush=True)
    subprocess.run([sys.executable, str(path)], check=True, cwd=TESTS.parent)

print(f"\nPASS: {len(files)} repository test files")
