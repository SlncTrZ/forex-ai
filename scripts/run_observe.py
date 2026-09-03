#!/usr/bin/env python3
from forex_ai.config import load_runtime_config
from forex_ai.runtime.observer import run_observer


if __name__ == "__main__":
    raise SystemExit(run_observer(load_runtime_config()))
