from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    anthropic_api_key: str = ""
    database_url: str = "postgresql+asyncpg://lila:lila@localhost:5432/lila"
    api_base_url: str = "http://localhost:8000"
    api_internal_token: str = "change-me"
    tribute_webhook_secret: str = "change-me"
    price_second_game_rub: int = 299
    llm_model: str = "claude-opus-5"
    log_level: str = "INFO"
    env: str = "local"

    turn_limit: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
