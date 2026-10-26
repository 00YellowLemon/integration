"""Security utilities: CSRF OAuth state generation/verification and AES-GCM token encryption."""
import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Union
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _derive_32byte_key(secret_key: Union[str, bytes]) -> bytes:
    """Derive a fixed 32-byte key for AESGCM from secret_key."""
    if isinstance(secret_key, str):
        secret_bytes = secret_key.encode("utf-8")
    else:
        secret_bytes = secret_key

    # If secret_key is a 64-char hex string, try hex decoding first
    if isinstance(secret_key, str) and len(secret_key) == 64:
        try:
            raw_key = bytes.fromhex(secret_key)
            if len(raw_key) == 32:
                return raw_key
        except ValueError:
            pass

    # Fallback to SHA256 digest to guarantee 32 bytes
    return hashlib.sha256(secret_bytes).digest()


def generate_oauth_state(secret_key: str) -> str:
    """
    Generate a cryptographically signed CSRF state string containing timestamp and nonce.
    Format: <timestamp_hex>.<nonce_hex>.<signature_hex>
    """
    key_bytes = _derive_32byte_key(secret_key)
    timestamp = int(time.time())
    ts_hex = hex(timestamp)[2:]
    nonce_hex = secrets.token_hex(16)
    payload = f"{ts_hex}.{nonce_hex}"
    signature = hmac.new(key_bytes, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_oauth_state(state: str, secret_key: str, max_age_seconds: int = 600) -> bool:
    """
    Verify the validity, integrity, and age of an OAuth CSRF state string.
    Returns True if state is valid and within max_age_seconds, False otherwise.
    """
    if not state or not isinstance(state, str):
        return False

    parts = state.split(".")
    if len(parts) != 3:
        return False

    ts_hex, nonce_hex, provided_sig = parts
    payload = f"{ts_hex}.{nonce_hex}"

    key_bytes = _derive_32byte_key(secret_key)
    expected_sig = hmac.new(key_bytes, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(provided_sig, expected_sig):
        return False

    try:
        ts = int(ts_hex, 16)
    except ValueError:
        return False

    now = int(time.time())
    # Reject if in future beyond 5 seconds (clock skew) or older than max_age_seconds
    if ts > now + 5 or (now - ts) > max_age_seconds:
        return False

    return True


def encrypt_token(plain_token: str, secret_key: str) -> str:
    """
    Encrypt a plaintext token using AES-256-GCM.
    Returns URL-safe base64 encoded ciphertext (nonce + ciphertext + tag).
    """
    if plain_token is None:
        raise ValueError("Plain text token cannot be None")

    key = _derive_32byte_key(secret_key)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # Standard 96-bit nonce for AES-GCM
    encrypted = aesgcm.encrypt(nonce, plain_token.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + encrypted).decode("utf-8")


def decrypt_token(cipher_text: str, secret_key: str) -> str:
    """
    Decrypt an AES-256-GCM encrypted token string.
    Raises ValueError on invalid ciphertext or key mismatch.
    """
    if not cipher_text:
        raise ValueError("Cipher text cannot be empty")

    key = _derive_32byte_key(secret_key)
    try:
        raw_data = base64.urlsafe_b64decode(cipher_text.encode("utf-8"))
        if len(raw_data) < 28:  # 12 bytes nonce + at least 16 bytes auth tag
            raise ValueError("Cipher text too short")
        nonce = raw_data[:12]
        encrypted = raw_data[12:]
        aesgcm = AESGCM(key)
        decrypted = aesgcm.decrypt(nonce, encrypted, None)
        return decrypted.decode("utf-8")
    except Exception as e:
        raise ValueError(f"Failed to decrypt token: {e}") from e
