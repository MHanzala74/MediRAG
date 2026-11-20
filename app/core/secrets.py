"""Encryption at rest for API keys that admins enter in the Settings page (Fernet / AES-128-CBC + HMAC)."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

PREFIX = "enc:v1:"
_DEFAULT_JWT = "change-me-in-production"


class SecretError(RuntimeError):
    pass


def _fernet() -> Fernet:
    material = settings.settings_encryption_key or settings.jwt_secret_key
    key = base64.urlsafe_b64encode(hashlib.sha256(("medirag-settings-v1:" + material).encode()).digest())
    return Fernet(key)


def can_store_secrets() -> tuple[bool, str]:
    """Storing keys with the well-known default secret would be encryption in name only."""
    if settings.is_production and not settings.settings_encryption_key and settings.jwt_secret_key == _DEFAULT_JWT:
        return False, "Set JWT_SECRET_KEY (or SETTINGS_ENCRYPTION_KEY) to a private random value before storing API keys in production."
    return True, ""


def encrypt(plain: str) -> str:
    return PREFIX + _fernet().encrypt(plain.encode()).decode()


def decrypt(stored: str) -> str:
    if not stored.startswith(PREFIX):
        raise SecretError("Not an encrypted value")
    try:
        return _fernet().decrypt(stored[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        raise SecretError("Cannot decrypt (the encryption key changed). Enter the API key again.") from e


def mask(secret: str | None) -> str | None:
    """'••••ab12' — never more than the last 4 characters."""
    if not secret:
        return None
    return "••••" + (secret[-4:] if len(secret) >= 12 else "")
