#!/usr/bin/env python3
"""Compatibility entry for the two current process-boundary mutation contracts.

The previous optimization recipe builder only compiled binaries and did not test
whether they changed observable behavior. The real resource suites retain their
scalar, schema/cache, expression, timing and memory contracts. This entry proves
output and descendant-process guards with complete baseline/mutant evidence.
Obsolete compile-only recipe names are rejected by the shared runner.
"""
from mutation_support import legacy_main

if __name__ == "__main__":
    raise SystemExit(legacy_main(["process-output-budget", "process-tree-cleanup"], "ablate-bounded", "resources"))
