#!/usr/bin/env python3
"""Compile resource Rename counterfactuals and require their exact E2E failures."""
import pathlib
import sys

from mutation_support import main

output = sys.argv[1] if len(sys.argv) == 2 else str(pathlib.Path(__file__).resolve().parents[2] / 'artifacts/resource-rename-ablation')
if len(sys.argv) > 2:
    raise SystemExit('usage: ablate-resource-rename.py [NEW_OUTPUT_DIRECTORY]')
raise SystemExit(main(['audit', output, '--variants', 'resource-rename-unique', 'resource-rename-capture']))
