from functools import lru_cache

import anthropic

from src.config import get_settings


@lru_cache
def get_client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    if settings.anthropic_api_key:
        return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    # Falls back to ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login` profile
    # resolution built into the SDK — see docs/PLAN.md §1.
    return anthropic.AsyncAnthropic()


def extract_text(message: anthropic.types.Message) -> str:
    """Pull the visible text out of a Messages API response, skipping thinking blocks."""
    parts = [block.text for block in message.content if block.type == "text"]
    return "\n".join(parts).strip()
