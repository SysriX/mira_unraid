"""WireGuard keys (Curve25519), pre-shared keys and QR codes."""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from wgcore import WgError

_RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def generate_keypair() -> tuple[str, str]:
    """Returns (private_key, public_key), both base64 like `wg genkey | wg pubkey`."""
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                  serialization.NoEncryption())
    return base64.b64encode(priv_raw).decode(), base64.b64encode(priv.public_key().public_bytes(*_RAW)).decode()


def public_from_private(private_key: str) -> str:
    try:
        raw = base64.b64decode(private_key.strip(), validate=True)
        if len(raw) != 32:
            raise ValueError
        priv = X25519PrivateKey.from_private_bytes(raw)
    except Exception as e:  # noqa: BLE001
        raise WgError("Das ist kein gültiger privater WireGuard-Schlüssel") from e
    return base64.b64encode(priv.public_key().public_bytes(*_RAW)).decode()


def generate_psk() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def valid_key(k: str) -> bool:
    try:
        return len(base64.b64decode((k or "").strip(), validate=True)) == 32
    except Exception:  # noqa: BLE001
        return False


def qr_data_uri(text: str) -> str | None:
    try:
        import segno
    except ImportError:
        return None
    return segno.make(text, error="m").svg_data_uri(scale=5, border=2, dark="#000", light="#fff")
