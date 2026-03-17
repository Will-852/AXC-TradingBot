#!/usr/bin/env python3
"""Entry point for AXC Dashboard."""
import os
import sys

HOME = os.environ.get("AXC_HOME", os.path.expanduser("~/projects/axc-trading"))
if HOME not in sys.path:
    sys.path.insert(0, HOME)

from scripts.dashboard.server import main

if __name__ == "__main__":
    main()
