from functools import lru_cache

from pydantic import AnyHttpUrl, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process environment for FastAPI, Lambdas, and the CLI.

    Timeouts, quotas, and SQS receive counts are locked in
    docs/timeouts-quotas.md. They are not loaded from the environment.
    """

    model_config = SettingsConfigDict( #tells pydantic how to load the class(where to look, properties to enforce)
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    database_url: PostgresDsn
    database_direct_url: PostgresDsn
    supabase_jwt_secret: SecretStr
    web_origin: AnyHttpUrl


@lru_cache
def get_settings() -> Settings: #used to get settings object - for db connections and more
    return Settings()
