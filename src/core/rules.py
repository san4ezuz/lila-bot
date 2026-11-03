from collections import deque
from dataclasses import replace

from src.core.board import BOARD_MAX_CELL, ENTRY_CELL, FINISH_CELL, Board
from src.core.dice import ENTRY_GUARANTEE_ATTEMPTS
from src.core.models import GameState, MoveEvent, MoveResult, TransitionType

# 60-turn cap (docs/RULES.md §5) — a game that hasn't reached cell 68 by then is force-finished.
# Paired with ASSIST_FROM_TURN below, which steers the dice toward the finish well before the
# cap would bite, so hitting turn_limit_reached should be rare in practice.
DEFAULT_TURN_LIMIT: int | None = 60

# From this turn onward, GameService stops rolling truly randomly and instead walks the player
# along the shortest remaining path to the finish (see shortest_path_rolls() below) — see
# docs/RULES.md §5. Leaves a 20-turn cushion before DEFAULT_TURN_LIMIT for the shortest path
# (rarely more than a handful of rolls) plus any extra rolls from natural sixes along the way.
ASSIST_FROM_TURN = 40

# Classic rule is "need a 6 to enter" — reinstated after players found the un-gated entry didn't
# match the game's own framing (see docs/RULES.md §2). Entry is guaranteed by this attempt
# regardless of what's rolled; the odds of a natural six also climb on each attempt before that
# (see dice.roll_entry_die), so this fallback is defense-in-depth and rarely the thing that
# actually fires.


def create_initial_state() -> GameState:
    return GameState()


def is_finished(state: GameState) -> bool:
    return state.finished


def apply_roll(
    state: GameState,
    roll: int,
    board: Board,
    turn_limit: int | None = DEFAULT_TURN_LIMIT,
) -> tuple[GameState, MoveResult]:
    """Apply a single dice roll to a game state. Pure function: same inputs -> same outputs.

    Must not be called on an already-finished state — callers are expected to check
    `state.finished` (surfaced to clients as `can_roll`) before offering another roll.
    """
    if state.finished:
        raise ValueError("cannot roll on a finished game")
    if not 1 <= roll <= 6:
        raise ValueError(f"roll out of range: {roll}")

    next_turn = state.turn + 1

    if not state.in_play:
        if roll == ENTRY_CELL or next_turn >= ENTRY_GUARANTEE_ATTEMPTS:
            new_state, result = _move(
                next_turn, roll, None, ENTRY_CELL, ENTRY_CELL, None, "entered", extra_roll=True
            )
        else:
            new_state, result = _move(
                next_turn, roll, None, None, None, None, "not_entered", extra_roll=False
            )
        return _apply_turn_limit(new_state, result, turn_limit)

    assert state.position is not None
    from_position = state.position
    raw_position = from_position + roll

    if raw_position > BOARD_MAX_CELL:
        new_state, result = _move(
            next_turn,
            roll,
            from_position,
            raw_position,
            from_position,
            None,
            "blocked_overshoot",
            extra_roll=roll == 6,
        )
        return _apply_turn_limit(new_state, result, turn_limit)

    final_position = raw_position
    transition: TransitionType | None = None
    if raw_position != FINISH_CELL:
        transition_lookup = board.resolve_transition(raw_position)
        if transition_lookup is not None:
            final_position, transition = transition_lookup

    finished = final_position == FINISH_CELL
    new_state, result = _move(
        next_turn,
        roll,
        from_position,
        raw_position,
        final_position,
        transition,
        "finished" if finished else "moved",
        extra_roll=(roll == 6) and not finished,
    )
    return _apply_turn_limit(new_state, result, turn_limit)


def _move(
    turn: int,
    roll: int,
    from_position: int | None,
    raw_position: int | None,
    final_position: int | None,
    transition: TransitionType | None,
    event: MoveEvent,
    *,
    extra_roll: bool,
) -> tuple[GameState, MoveResult]:
    """Build the post-roll state and its MoveResult. The player is on the board iff they have a
    final position; only reaching the finish cell (event "finished") ends the game here — the
    turn limit is applied separately by _apply_turn_limit()."""
    finished = event == "finished"
    new_state = GameState(
        position=final_position,
        in_play=final_position is not None,
        turn=turn,
        finished=finished,
        finish_reason="enlightenment" if finished else None,
    )
    result = MoveResult(
        turn=turn,
        roll=roll,
        from_position=from_position,
        raw_position=raw_position,
        final_position=final_position,
        transition=transition,
        event=event,
        extra_roll=extra_roll,
        finished=finished,
    )
    return new_state, result


def _apply_turn_limit(
    state: GameState, result: MoveResult, turn_limit: int | None
) -> tuple[GameState, MoveResult]:
    if turn_limit is None or state.finished or state.turn < turn_limit:
        return state, result

    return (
        replace(state, finished=True, finish_reason="turn_limit"),
        replace(result, event="turn_limit_reached", extra_roll=False, finished=True),
    )


def shortest_path_rolls(position: int, board: Board) -> list[int] | None:
    """Breadth-first search over the exact movement rules in apply_roll() (overshoot blocking,
    arrow/snake transitions) for the shortest sequence of rolls that carries `position` to
    FINISH_CELL. Used by GameService from ASSIST_FROM_TURN onward to steer a lagging player home
    before DEFAULT_TURN_LIMIT hits — see docs/RULES.md §5.

    Returns the roll sequence (each 1-6, first roll first), or None if `position` is already the
    finish cell or (should never happen on the real board) no path exists.
    """
    if position == FINISH_CELL:
        return None

    prev: dict[int, tuple[int, int]] = {}  # reached position -> (from position, roll that got there)
    visited = {position}
    queue: deque[int] = deque([position])

    while queue:
        current = queue.popleft()
        for roll in range(1, 7):
            raw = current + roll
            if raw > BOARD_MAX_CELL:
                continue
            if raw == FINISH_CELL:
                target = FINISH_CELL
            else:
                transition = board.resolve_transition(raw)
                target = transition[0] if transition is not None else raw

            if target == FINISH_CELL:
                path = [roll]
                cursor = current
                while cursor != position:
                    cursor, prior_roll = prev[cursor]
                    path.append(prior_roll)
                path.reverse()
                return path

            if target not in visited:
                visited.add(target)
                prev[target] = (current, roll)
                queue.append(target)

    return None
