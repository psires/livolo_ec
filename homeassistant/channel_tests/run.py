#!/usr/bin/env python3
"""Load HA's HTTP compatibility aliases before pytest plugins import HTTP clients."""

import homeassistant  # noqa: F401
import pytest

if __name__ == "__main__":
    raise SystemExit(pytest.console_main())
