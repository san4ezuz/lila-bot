"""Crisis mode: for a while after the player's text trips the crisis screen (src/llm/safety.py),
the product steps back — replies carry help contacts, reminders stop, and the paywall is replaced
by those contacts. Time-boxed rather than permanent so one dark message doesn't mark someone
for good; each new trigger restarts the window."""

from datetime import UTC, datetime, timedelta

from src.db.models import User

CRISIS_MODE_DURATION = timedelta(days=14)


def in_crisis_mode(user: User, now: datetime | None = None) -> bool:
    if user.crisis_at is None:
        return False
    return (now or datetime.now(UTC)) - user.crisis_at < CRISIS_MODE_DURATION
