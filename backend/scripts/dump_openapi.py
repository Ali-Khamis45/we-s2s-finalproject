"""Write the OpenAPI schema to docs/openapi.json.

This file is the contract between the two halves of the project. The frontend's
TypeScript types are generated from it, and a committed copy means the
generation step needs no running server — so CI can regenerate and diff without
booting the app or downloading a model.

Run it whenever a route or schema changes:

    python backend/scripts/dump_openapi.py

CI runs the same command and fails if the result differs from what is
committed, which is what stops the frontend drifting away from the backend
silently.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

# Importing the app must not need a secret, a database, or a model.
os.environ.setdefault("SCC_DEBUG", "true")
os.environ.setdefault("SCC_MOSHI_ENABLED", "false")

from app.main import app  # noqa: E402

OUT = REPO / "docs" / "openapi.json"


def main() -> int:
    schema = app.openapi()

    # The version string moves with the package and would otherwise churn the
    # diff on every release; the paths and schemas are what matter here.
    schema.setdefault("info", {})["version"] = "contract"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so the file is byte-stable across runs and Python versions —
    # a diff should mean the API changed, never that a dict reordered.
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"

    changed = not OUT.exists() or OUT.read_text(encoding="utf-8") != text
    check_only = "--check" in sys.argv

    # In --check mode the file is never touched. CI runs this to detect drift,
    # and a checker that rewrites the thing it is checking leaves a dirty tree
    # and makes a follow-up `git diff --exit-code` meaningless.
    if not check_only:
        OUT.write_text(text, encoding="utf-8")

    paths = len(schema.get("paths", {}))
    models = len(schema.get("components", {}).get("schemas", {}))
    state = "stale" if (check_only and changed) else ("updated" if changed else "unchanged")
    print(f"{state}: {OUT.relative_to(REPO)}")
    print(f"  {paths} paths, {models} schemas")

    if check_only and changed:
        print(
            "\nThe committed schema is out of date. Run:\n"
            "    python backend/scripts/dump_openapi.py\n"
            "and commit the result together with the change that caused it.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
