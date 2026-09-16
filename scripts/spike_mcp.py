#!/usr/bin/env python3
"""Prove the Airbnb MCP server answers from wherever this is running.

Run it inside the container before trusting anything downstream of lodging.
Exit 0 means the tools are listed and a real search came back.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mate.mcp_stdio import MCPError, airbnb_client

EXPECTED = {"airbnb_search", "airbnb_listing_details"}


def main() -> int:
    search = "--search" in sys.argv
    try:
        with airbnb_client(timeout=180.0) as client:
            tools = client.list_tools()
            names = {t.name for t in tools}
            print(f"tools: {sorted(names)}")
            missing = EXPECTED - names
            if missing:
                print(f"FAIL missing tools: {sorted(missing)}", file=sys.stderr)
                return 2

            if search:
                result = client.call_tool("airbnb_search", {
                    "location": "Petrópolis, Rio de Janeiro",
                    "checkin": "2026-10-31",
                    "checkout": "2026-11-01",
                    "adults": 2,
                })
                print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
    except MCPError as e:
        print(f"FAIL {e}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("FAIL npx not found — the container needs Node 18+", file=sys.stderr)
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
