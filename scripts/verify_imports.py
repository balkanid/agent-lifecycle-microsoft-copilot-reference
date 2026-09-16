#!/usr/bin/env python3
"""CI smoke test: every script imports cleanly and exposes main()."""

from __future__ import annotations

import importlib
import pathlib
import sys

MODULES = [
    "balkanid_api",
    "gate",
    "entra_agent_identity_gate",
    "sailpoint_gate",
    "discover_agents",
    "publish_copilot_agent",
    "publish_declarative_agent",
    "preflight",
    "trigger_sync",
    "verify_approved_scope",
]


def main() -> int:
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    failures: list[str] = []

    for name in MODULES:
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - report every failure, not just the first
            failures.append(f"{name}: import failed: {exc}")
            continue
        if name != "balkanid_api" and not callable(getattr(module, "main", None)):
            failures.append(f"{name}: no callable main()")
        print(f"ok  {name}")

    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
