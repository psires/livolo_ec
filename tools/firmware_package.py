#!/usr/bin/env python3
"""Repository entry point for signed firmware package/build operations."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from livolod.update_package import *

if __name__ == "__main__":
    main()
