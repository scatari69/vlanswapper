#!/usr/bin/env python3
"""Тонкая обёртка для запуска ``./vlanswapper.py`` без установки пакета."""

import sys

from vlanswapper.cli import run

if __name__ == "__main__":
    sys.exit(run())
