"""Alerts to the admins in Telegram (ADMIN_TELEGRAM_IDS — set it on the api service too, not only
on bot). The LLM fallback is silent for the player by design, which is exactly why a broken
LLM_MODEL once went unnoticed: these alerts are the other half of that design.

State is in-process memory: a restart forgets the throttle, which at worst repeats one alert.
"""

import asyncio
import logging
import math
import time
from collections import deque

from src.config import get_settings
from src.llm.client import LlmResult
from src.services import notify

logger = logging.getLogger(__name__)

# One alert per key per this long, however often the problem repeats.
_THROTTLE_SECONDS = 30 * 60

# Fallbacks and early stops are normal one at a time (a timeout, a long reply); this many within
# the window is an outage.
_LLM_PROBLEM_WINDOW_SECONDS = 10 * 60
_LLM_PROBLEM_THRESHOLD = 5

# Errors no retry fixes: a bad key, a bad LLM_MODEL (404), an empty Anthropic balance.
_CRITICAL_LLM_ERRORS = (
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "credit balance",
    "TypeError",
)

_last_sent: dict[str, float] = {}
_llm_problems: deque[float] = deque()
_background: set[asyncio.Task] = set()


async def send_alert(key: str, text: str) -> bool:
    """Returns whether the alert went out (False when throttled or no admin is configured)."""
    now = time.monotonic()
    if now - _last_sent.get(key, -math.inf) < _THROTTLE_SECONDS:
        return False
    _last_sent[key] = now
    logger.error("alert %s: %s", key, text)
    admin_ids = get_settings().admin_telegram_ids
    if not admin_ids:
        return False
    for telegram_id in admin_ids:
        await notify.send_message(telegram_id, f"⚠️ {text}")
    return True


def fire_alert(key: str, text: str) -> None:
    """send_alert() in the background, so the player's request doesn't wait on Telegram."""
    task = asyncio.create_task(send_alert(key, text))
    _background.add(task)
    task.add_done_callback(_background.discard)


def note_llm_result(result: LlmResult) -> None:
    if result.skipped:
        return
    if result.error and any(marker in result.error for marker in _CRITICAL_LLM_ERRORS):
        fire_alert(
            "llm_critical",
            f"LLM не отвечает ({result.model}): {result.error}. Игроки получают запасной текст.",
        )
        return
    if not result.fallback and result.stop_reason in (None, "end_turn"):
        return

    now = time.monotonic()
    _llm_problems.append(now)
    while _llm_problems and now - _llm_problems[0] > _LLM_PROBLEM_WINDOW_SECONDS:
        _llm_problems.popleft()
    if len(_llm_problems) >= _LLM_PROBLEM_THRESHOLD:
        reason = result.error or f"stop_reason={result.stop_reason}"
        fire_alert(
            "llm_degraded",
            f"{len(_llm_problems)} сбоев LLM за 10 минут (фолбэк или stopped early). "
            f"Последний: {result.kind}, {reason}.",
        )


def reset() -> None:
    """Tests only."""
    _last_sent.clear()
    _llm_problems.clear()
