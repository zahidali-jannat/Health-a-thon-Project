"""Password hashing, session tokens and ID generation - standard library only."""

import hashlib
import hmac
import os
import secrets

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)
# A fixed hash to verify against when the account doesn't exist, so response time doesn't reveal it.
_DUMMY_HASH = None

# No 0/O/1/I/L: IDs are read aloud and typed by people.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    global _DUMMY_HASH
    if stored is None:
        _DUMMY_HASH = _DUMMY_HASH or hash_password("not-a-real-password")
        stored, real = _DUMMY_HASH, False
    else:
        real = True
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex) and real


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_clinician_code() -> str:
    return "CLN-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


def new_temp_password() -> str:
    """Easy to read aloud and type: e.g. 7KQM-3XPR. Only its hash is stored."""
    chars = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{chars[:4]}-{chars[4:]}"


PATIENT_MIN_PASSWORD = 6


def check_patient_password(password: str) -> str | None:
    """Returns a user-facing problem, or None if the password is acceptable."""
    if len(password) < PATIENT_MIN_PASSWORD:
        return f"Please choose a password with at least {PATIENT_MIN_PASSWORD} characters."
    return None


def new_login_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def login_code_hash(patient_id: str, code: str) -> str:
    return hashlib.sha256(f"{patient_id}:{code}".encode()).hexdigest()


def normalize_phone(raw: str) -> str:
    """Keep digits; use the last 10 (Indian mobile numbers). Empty string if too short."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""
