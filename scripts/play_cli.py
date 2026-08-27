"""Simulate N random Lila games with the pure rules engine (no DB, no LLM, no Telegram).

Usage:
    python scripts/play_cli.py [n_games]

Prints aggregate stats: average game length, share finishing by exact landing on 68 vs.
hitting the turn limit. This is the product check from docs/PLAN.md §5.4 — run it before
wiring up LLM/money, and rerun it whenever data/transitions.json or the turn limit changes.

Games start not-yet-entered (create_initial_state()), matching GameService.set_intention() —
the "roll a 6 to enter" gate is back in the real product flow (docs/RULES.md §2), with entry
guaranteed by ENTRY_GUARANTEE_ATTEMPTS rolls so it can't drag on indefinitely.

Product default has no turn limit (docs/RULES.md §5 — removed after feedback that capping
partial partии felt arbitrary), so `turn_limit_reached` won't show up in a default run of this
script; pass a second CLI arg to still exercise/measure that path if useful.
"""

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.board import get_default_board
from src.core.dice import roll_dice
from src.core.rules import DEFAULT_TURN_LIMIT, apply_roll, create_initial_state


def play_one_game(rng: random.Random, turn_limit: int | None) -> tuple[int, str]:
    """Returns (turns_used, finish_reason)."""
    board = get_default_board()
    state = create_initial_state()

    while not state.finished:
        roll = roll_dice(rng)
        state, result = apply_roll(state, roll, board, turn_limit=turn_limit)

    return state.turn, state.finish_reason or "unknown"


def main() -> None:
    n_games = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    turn_limit = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_TURN_LIMIT
    rng = random.Random(42)

    lengths: list[int] = []
    reasons: dict[str, int] = {}

    for _ in range(n_games):
        turns, reason = play_one_game(rng, turn_limit)
        lengths.append(turns)
        reasons[reason] = reasons.get(reason, 0) + 1

    print(f"Games simulated: {n_games} (turn_limit={turn_limit})")
    print(f"Average turns per game: {statistics.mean(lengths):.1f}")
    print(f"Median turns per game:  {statistics.median(lengths)}")
    print(f"Min / max turns:        {min(lengths)} / {max(lengths)}")
    print()
    print("Finish reason breakdown:")
    for reason, count in sorted(reasons.items()):
        pct = 100 * count / n_games
        print(f"  {reason:15s} {count:6d}  ({pct:5.1f}%)")


if __name__ == "__main__":
    main()
