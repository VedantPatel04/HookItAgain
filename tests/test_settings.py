import pytest
from pydantic import ValidationError

from hookitagain.settings import Settings


def test_settings_fail_fast_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "DATABASE_URL",
        "DATABASE_DIRECT_URL",
        "SUPABASE_URL",
        "SUPABASE_JWT_SECRET",
        "WEB_ORIGIN",
        "SECRET_ENCRYPTION_KEY",
        "INGEST_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
