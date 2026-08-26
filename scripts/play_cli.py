"""Simulate N random Lila games with the pure rules engine (no DB, no LLM, no Telegram).

Usage:
    python scripts/play_cli.py [n_games]

Prints aggregate stats: average game length, share finishing by exact landing on 68 vs.
hitting the turn limit, share that never manage to enter the game at all. This is the
product check from docs/PLAN.md §5.4 — run it before wiring up LLM/money, and rerun it
whenever data/transitions.json or the turn limit changes.
"""

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.board import get_default_board
from src.core.dice import roll_dice
from src.core.rules import DEFAULT_TURN_LIMIT, create_initial_state, apply_roll


def play_one_game(rng: random.Random, turn_limit: int) -> tuple[int, str, bool]:
    """Returns (turns_used, finish_reason, ever_entered)."""
    board = get_default_board()
    state = create_initial_state()
    ever_entered = False

    while not state.finished:
        roll = roll_dice(rng)
        state, result = apply_roll(state, roll, board, turn_limit=turn_limit)
        if state.in_play:
            ever_entered = True

    return state.turn, state.finish_reason or "unknown", ever_entered


def main() -> None:
    n_games = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    turn_limit = DEFAULT_TURN_LIMIT
    rng = random.Random(42)

    lengths: list[int] = []
    reasons: dict[str, int] = {}
    never_entered = 0

    for _ in range(n_games):
        turns, reason, entered = play_one_game(rng, turn_limit)
        lengths.append(turns)
        reasons[reason] = reasons.get(reason, 0) + 1
        if not entered:
            never_entered += 1

    print(f"Games simulated: {n_games} (turn_limit={turn_limit})")
    print(f"Average turns per game: {statistics.mean(lengths):.1f}")
    print(f"Median turns per game:  {statistics.median(lengths)}")
    print(f"Min / max turns:        {min(lengths)} / {max(lengths)}")
    print()
    print("Finish reason breakdown:")
    for reason, count in sorted(reasons.items()):
        pct = 100 * count / n_games
        print(f"  {reason:15s} {count:6d}  ({pct:5.1f}%)")
    print()
    print(f"Games that never entered play (all rolls < 6): {never_entered} ({100 * never_entered / n_games:.2f}%)")


if __name__ == "__main__":
    main()
