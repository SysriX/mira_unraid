"""wgcore – WireGuard management for OPNsense, shared by VERA (standalone) and MIRA (module).

The canonical copy lives in the VERA repository (app/wgcore). MIRA carries an identical copy;
change it here first and copy it over (see README).
"""
from __future__ import annotations

__version__ = "1.0.0"


class WgError(Exception):
    """Expected, user-facing error (bad input, OPNsense validation, ...). Hosts answer with HTTP 400."""
