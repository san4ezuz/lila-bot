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


def supports_effort(model: str) -> bool:
    """`output_config.effort` 400s on older/smaller models (confirmed for Haiku 4.5 — see
    claude-api skill's thinking/effort reference table). Haiku 5.5 does support it, and needs it:
    its default effort spends the token budget on thinking. Keep this an allowlist-by-exclusion of
    known non-supporting families rather than a hardcoded model list, since docs/PLAN.md §5's
    LLM_MODEL is operator-configurable and may change during testing."""
    return not any(family in model.lower() for family in ("haiku-3", "haiku-4"))
