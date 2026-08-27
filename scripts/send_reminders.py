"""Sends the abandoned-game reminder cascade (docs/UX_ITERATION_1.md §2) — 24h / 3d / 7d since
last activity, capped at 3 nudges per game, opt-out and snooze respected. Meant to run on a
schedule — e.g. a Railway Cron Job service running

    python scripts/send_reminders.py

hourly, sharing the same DATABASE_URL/BOT_TOKEN as the api/bot services. Cheap to run often:
each game is only ever nudged once per stage regardless of call frequency.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.session import get_sessionmaker
from src.services.reminder_service import ReminderService


async def main() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        service = ReminderService(session)
        count = await service.send_due_reminders()
        await session.commit()
    print(f"Sent {count} reminder(s).")


if __name__ == "__main__":
    asyncio.run(main())
