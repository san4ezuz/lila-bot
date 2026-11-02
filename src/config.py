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
    # When set, only purchases of this digital product grant a game. Unset accepts any product.
    tribute_product_id: int | None = None
    price_second_game_rub: int = 299
    llm_model: str = "claude-haiku-5-5"
    log_level: str = "INFO"
    env: str = "local"
    railway_environment_name: str = ""  # set by Railway on every deployed service

    # 60-turn cap, paired with the dice-assist mechanic from turn 40 (docs/RULES.md §5). Override
    # with TURN_LIMIT in .env; set to empty/None to disable the cap entirely (also turns off the
    # assist, which only kicks in when a turn_limit is configured).
    turn_limit: int | None = 60
    admin_telegram_ids_raw: str = Field(default="", alias="ADMIN_TELEGRAM_IDS")

    @property
    def is_production(self) -> bool:
        return self.env == "prod" or bool(self.railway_environment_name)

    def ensure_production_secrets(self) -> None:
        """Refuses to boot a deployed API on the public placeholder values: with them anyone could
        call the API as the bot, or forge a signed Tribute webhook and grant themselves games."""
        if not self.is_production:
            return
        placeholders = {"", "change-me"}
        unset = [
            name
            for name, value in (
                ("API_INTERNAL_TOKEN", self.api_internal_token),
                ("TRIBUTE_API_KEY", self.tribute_api_key),
            )
            if value in placeholders
        ]
        if unset:
            raise RuntimeError(f"{', '.join(unset)} must be set in production — see .env.example")

    @property
    def admin_telegram_ids(self) -> set[int]:
        return {
            int(part) for part in self.admin_telegram_ids_raw.split(",") if part.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
