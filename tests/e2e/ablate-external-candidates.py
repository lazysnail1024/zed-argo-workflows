#!/usr/bin/env python3
"""Historical entry point; use OUTPUT [--variants NAME ...] [--tools DIR]."""
from legacy_mutations import main

if __name__ == "__main__":
    raise SystemExit(main(entry='ablate-external-candidates.py'))
