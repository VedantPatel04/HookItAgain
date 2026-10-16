import pytest
from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from hookitagain.crypto import decrypt_secret, encrypt_secret
from hookitagain.settings import Settings


def test_encrypt_decrypt_roundtrip() -> None:
    key = Fernet.generate_key().decode()
    plaintext = "source-hmac-secret"
    ciphertext = encrypt_secret(plaintext, key=key)
    assert ciphertext != plaintext.encode()
    assert decrypt_secret(ciphertext, key=key) == plaintext


def test_wrong_key_cannot_decrypt() -> None:
    key = Fernet.generate_key().decode()
    other = Fernet.generate_key().decode()
    ciphertext = encrypt_secret("source-hmac-secret", key=key)
    with pytest.raises(InvalidToken):
        decrypt_secret(ciphertext, key=other)


def test_settings_reject_non_fernet_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/hookitagain")
    monkeypatch.setenv(
        "DATABASE_DIRECT_URL",
        "postgresql://postgres:postgres@localhost:5432/hookitagain",
    )
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "ci-test-secret")
    monkeypatch.setenv("WEB_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", "not-a-fernet-key")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
