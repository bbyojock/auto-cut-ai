#!/usr/bin/env python3
"""Run the Version Next mobile backend API server."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

# Allow running directly via `python scripts/run_mobile_api.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.mobile_api_service import create_app  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AutoCutAI mobile API server.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
