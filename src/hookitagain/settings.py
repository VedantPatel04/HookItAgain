from functools import lru_cache

from cryptography.fernet import Fernet
# pydantic settings packages :)
from pydantic import AnyHttpUrl, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _require_fernet(value: SecretStr) -> SecretStr:
    # wrong key shape fails at boot
    try:
        Fernet(value.get_secret_value().encode())
    except (ValueError, TypeError) as exc:
        raise ValueError("SECRET_ENCRYPTION_KEY must be a Fernet key") from exc
    return value


class Settings(BaseSettings):
    """Process environment for FastAPI and the CLI.

    Timeouts, quotas, and SQS receive counts are locked in
    docs/timeouts-quotas.md. They are not loaded from the environment.
    Lambdas use LambdaSettings, not this class.
    """

    model_config = SettingsConfigDict( #tells pydantic how to load the class(where to look, properties to enforce)
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    database_url: PostgresDsn
    database_direct_url: PostgresDsn
    supabase_url: AnyHttpUrl
    supabase_jwt_secret: SecretStr
    web_origin: AnyHttpUrl
    secret_encryption_key: SecretStr #SecretStr type
    ingest_base_url: AnyHttpUrl

    @field_validator("secret_encryption_key")
    @classmethod
    def secret_encryption_key_must_be_fernet(cls, value: SecretStr) -> SecretStr:
        return _require_fernet(value)


class LambdaSettings(BaseSettings):
    # ingest, worker, reconciler. no WEB_ORIGIN, no Supabase JWT, no .env file
    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        frozen=True,
    )

    database_url: PostgresDsn
    secret_encryption_key: SecretStr
    delivery_queue_url: AnyHttpUrl

    @field_validator("secret_encryption_key")
    @classmethod
    def secret_encryption_key_must_be_fernet(cls, value: SecretStr) -> SecretStr:
        return _require_fernet(value)


@lru_cache
def get_settings() -> Settings: #used to get settings object - for db connections and more
    return Settings()


@lru_cache
def get_lambda_settings() -> LambdaSettings:
    # DATABASE_URL, SECRET_ENCRYPTION_KEY, DELIVERY_QUEUE_URL. not get_settings()
    return LambdaSettings()
