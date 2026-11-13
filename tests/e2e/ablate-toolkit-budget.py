#!/usr/bin/env python3
"""Prove the current real-process YAML depth boundary with one off-by-one fault.

The old archive-only abort comparison could accept unrelated process failures.
This compatibility entry now requires a passing current build and complete named
assertions; it does not need an unpublished historical source archive.
"""
from mutation_support import legacy_main

if __name__ == "__main__":
    raise SystemExit(legacy_main(["toolkit-depth-boundary"], "ablate-toolkit-budget", "contracts"))
