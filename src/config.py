from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    anthropic_api_key: str = ""
    database_url: str = "postgresql+asyncpg://lila:lila@localhost:5432/lila"
    api_base_url: str = "http://localhost:8000"
    api_internal_token: str = "change-me"
    # Same value as the "Api-Key" Tribute gives you in Creator Dashboard -> Settings -> API Keys —
    # it both authenticates outbound API calls AND signs inbound webhooks. See
    # src/services/billing_service.py's module docstring for how this was confirmed.
    tribute_api_key: str = "change-me"
    tribute_pay_url: str = ""  # static checkout link from Creator Dashboard; no API to mint one
    price_second_game_rub: int = 299
    llm_model: str = "claude-opus-5"
    log_level: str = "INFO"
    env: str = "local"

    turn_limit: int = 60
    admin_telegram_ids_raw: str = Field(default="", alias="ADMIN_TELEGRAM_IDS")

    @property
    def admin_telegram_ids(self) -> set[int]:
        return {
            int(part) for part in self.admin_telegram_ids_raw.split(",") if part.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
