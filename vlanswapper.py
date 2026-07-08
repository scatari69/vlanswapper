#!/usr/bin/env python3
"""Thin wrapper to run ``./vlanswapper.py`` without installing the package."""

import sys

from vlanswapper.cli import run

if __name__ == "__main__":
    sys.exit(run())
