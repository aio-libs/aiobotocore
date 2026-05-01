#!/usr/bin/env python3
"""Emit the aiobotocore override/async registries as JSON.

One entry point used by BOTH the `check-async-need` skill at runtime (via
a Bash step) and the eval harness (via direct import) so both paths see
the exact same data. The registry schema:

    {
      "overrides": [ "ClientArgsCreator.get_client_args", ... ],
      "async_methods": [ "read", "send", "emit", "_emit", ... ],
      "aio_classes": [ "AioAWSResponse", "AioBaseClient", ... ]
    }

Run from repo root:

    uv run python plugins/aiobotocore-bot/scripts/registries.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "plugins/aiobotocore-bot/evals"))

from _common import async_names, overridden_symbols  # noqa: E402


def main() -> int:
    method_names, class_names = async_names()
    payload = {
        "overrides": sorted(overridden_symbols()),
        "async_methods": sorted(method_names),
        "aio_classes": sorted(class_names),
    }
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
