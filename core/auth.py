"""App unlock PIN gate (hashed — never shown as a hint in the UI)."""

from __future__ import annotations

import hashlib
import hmac

# SHA-256 of the unlock PIN. Do not put the plaintext PIN in UI labels/tooltips.
_PIN_HASH = "9b4a4f40ff85f06c9ada8c7ad82d687c5f6ce461e211b391c104eb2f513968f3"


def verify_pin(pin: str) -> bool:
    """Constant-time compare of SHA-256(pin) against the unlock hash."""
    got = hashlib.sha256((pin or "").encode("utf-8")).hexdigest()
    return hmac.compare_digest(got, _PIN_HASH)


def pin_hash(pin: str) -> str:
    return hashlib.sha256((pin or "").encode("utf-8")).hexdigest()
