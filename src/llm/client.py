import asyncio
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import anthropic

from src.config import get_settings

logger = logging.getLogger(__name__)

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# What the call was for — the `kind` column of llm_calls.
LlmKind = Literal["not_entered", "interpret_cell", "deepen", "summary", "classifier"]


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


@dataclass(frozen=True, slots=True)
class LlmResult:
    """One model call as the game sees it: the text to show plus what it cost. `fallback` — the
    text is the static fallback (API error, timeout, empty reply); `skipped` — no call was made at
    all because an LLM limit was hit (src/services/llm_limits.py), so there's nothing to record."""

    text: str
    kind: LlmKind
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    stop_reason: str | None = None
    fallback: bool = False
    skipped: bool = False
    error: str | None = None
    duration_ms: int | None = None


def skipped_result(kind: LlmKind, fallback: str) -> LlmResult:
    return LlmResult(
        text=fallback, kind=kind, model=get_settings().llm_model, fallback=True, skipped=True
    )


async def call_model(
    *,
    kind: LlmKind,
    system: str | list[dict],
    content: str,
    max_tokens: int,
    effort: Effort,
    fallback: str,
    timeout: float,
) -> LlmResult:
    """The one place the Messages API is called. Never raises: on any failure the result carries
    `fallback` as its text and the reason in `error`, so the player's move is never lost
    (docs/PLAN.md §8.5)."""
    settings = get_settings()
    model = settings.llm_model
    kwargs: dict[str, Any] = {}
    if supports_effort(model):
        kwargs["output_config"] = {"effort": effort}
    started = time.monotonic()

    def _failed(error: str) -> LlmResult:
        return LlmResult(
            text=fallback,
            kind=kind,
            model=model,
            fallback=True,
            error=error[:500],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    try:
        # The dict literals below are the plain wire shapes from Anthropic's Messages API
        # (docs/PLAN.md §8.2); typing them as the SDK's exact TypedDicts is more ceremony than
        # value here, so this one call is exempted from strict overload matching.
        message = await asyncio.wait_for(
            get_client().messages.create(  # type: ignore[call-overload]
                model=model,
                max_tokens=max_tokens,
                system=system,  # type: ignore[arg-type]
                messages=[{"role": "user", "content": content}],
                **kwargs,
            ),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning("%s LLM call failed, using fallback: timeout", kind)
        return _failed("timeout")
    except anthropic.APIError as exc:
        logger.warning("%s LLM call failed, using fallback: %s", kind, exc)
        return _failed(f"{type(exc).__name__}: {exc}")
    except TypeError as exc:
        # The SDK raises a plain TypeError (not an AnthropicError subclass) when it can't
        # resolve credentials at request-build time — e.g. ANTHROPIC_API_KEY unset. Confirmed by
        # a live smoke test against a real running server (not just mocks): without this, a
        # misconfigured/missing key 500s the whole request instead of falling back.
        logger.error("%s LLM call misconfigured (likely missing API key), using fallback: %s", kind, exc)
        return _failed(f"TypeError: {exc}")

    text = extract_text(message)
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason != "end_turn":
        # Cut mid-sentence, or no text at all if thinking used up max_tokens. Silent otherwise —
        # the player just sees a clipped reply or the fallback.
        logger.warning("%s LLM call stopped early: %s, %d chars", kind, stop_reason, len(text))
    usage = getattr(message, "usage", None)
    return LlmResult(
        text=text or fallback,
        kind=kind,
        model=model,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None),
        stop_reason=stop_reason,
        fallback=not text,
        error=None if text else "empty reply",
        duration_ms=int((time.monotonic() - started) * 1000),
    )
