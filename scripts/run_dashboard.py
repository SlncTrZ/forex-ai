#!/usr/bin/env python3
"""Start Forex-AI's SSH-gated operator dashboard (Unix socket only)."""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from forex_ai.dashboard.server import serve

if __name__ == "__main__":
    serve()
