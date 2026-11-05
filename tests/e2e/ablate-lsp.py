#!/usr/bin/env python3
"""Existing mutation recipe, verified by the strict shared semantic gate."""
from mutation_support import legacy_main

if __name__ == "__main__":
    raise SystemExit(legacy_main(['document-version'], 'wf3-lsp-ablation'))
