#!/usr/bin/env python3
"""Compatibility entry point for the current, strict semantic mutation gate."""
from mutation_support import legacy_main

if __name__ == "__main__":
    raise SystemExit(legacy_main(['external-namespace'], 'ablate-external', group="contracts"))
