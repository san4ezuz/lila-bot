"""Marks games with no activity for 48h as abandoned and logs a `game_abandoned` event each
(docs/PLAN.md §12). Meant to run on a schedule — e.g. a Railway Cron Job service running

    python scripts/abandon_stale_games.py

once a day, sharing the same DATABASE_URL as the api/bot services. Not wired into request
handling: nothing blocks on this running, it only keeps metrics/user status accurate.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.session import get_sessionmaker
from src.services.game_service import GameService


async def main() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        service = GameService(session)
        count = await service.abandon_stale_games(hours=48)
        await session.commit()
    print(f"Marked {count} game(s) as abandoned.")


if __name__ == "__main__":
    asyncio.run(main())
