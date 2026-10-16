from cryptography.fernet import Fernet

from hookitagain.settings import get_settings


def _fernet(key: str | None = None) -> Fernet:
    # one process key wraps source and destination BYTEA ciphertext
    material = key if key is not None else get_settings().secret_encryption_key.get_secret_value()
    return Fernet(material.encode())


def encrypt_secret(plaintext: str, *, key: str | None = None) -> bytes:
    # stored as BYTEA. plaintext only in memory and on create/rotate
    return _fernet(key).encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes, *, key: str | None = None) -> str:
    # HMAC verify (ingest) and outbound sign (worker). list APIs never call this
    return _fernet(key).decrypt(ciphertext).decode("utf-8")
