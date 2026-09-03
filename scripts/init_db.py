#!/usr/bin/env python3
from forex_ai.config import load_runtime_config
from forex_ai.journal.db import initialize


def main() -> None:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    print(f"SQLite initialized: {cfg.db_path}")


if __name__ == "__main__":
    main()
