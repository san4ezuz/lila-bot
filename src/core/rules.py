from collections import deque

from src.core.board import BOARD_MAX_CELL, ENTRY_CELL, FINISH_CELL, Board
from src.core.dice import ENTRY_GUARANTEE_ATTEMPTS
from src.core.models import GameState, MoveEvent, MoveRecord, MoveResult

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
            new_state = GameState(
                position=ENTRY_CELL,
                in_play=True,
                turn=next_turn,
                history=state.history
                + (
                    MoveRecord(
                        turn=next_turn,
                        roll=roll,
                        from_position=None,
                        raw_position=ENTRY_CELL,
                        final_position=ENTRY_CELL,
                        transition=None,
                        event="entered",
                    ),
                ),
            )
            result = MoveResult(
                turn=next_turn,
                roll=roll,
                from_position=None,
                raw_position=ENTRY_CELL,
                final_position=ENTRY_CELL,
                transition=None,
                event="entered",
                extra_roll=True,
                finished=False,
            )
            return _apply_turn_limit(new_state, result, turn_limit)

        new_state = GameState(
            position=None,
            in_play=False,
            turn=next_turn,
            history=state.history
            + (
                MoveRecord(
                    turn=next_turn,
                    roll=roll,
                    from_position=None,
                    raw_position=None,
                    final_position=None,
                    transition=None,
                    event="not_entered",
                ),
            ),
        )
        result = MoveResult(
            turn=next_turn,
            roll=roll,
            from_position=None,
            raw_position=None,
            final_position=None,
            transition=None,
            event="not_entered",
            extra_roll=False,
            finished=False,
        )
        return _apply_turn_limit(new_state, result, turn_limit)

    assert state.position is not None
    from_position = state.position
    raw_position = from_position + roll

    if raw_position > BOARD_MAX_CELL:
        new_state = GameState(
            position=from_position,
            in_play=True,
            turn=next_turn,
            history=state.history
            + (
                MoveRecord(
                    turn=next_turn,
                    roll=roll,
                    from_position=from_position,
                    raw_position=raw_position,
                    final_position=from_position,
                    transition=None,
                    event="blocked_overshoot",
                ),
            ),
        )
        result = MoveResult(
            turn=next_turn,
            roll=roll,
            from_position=from_position,
            raw_position=raw_position,
            final_position=from_position,
            transition=None,
            event="blocked_overshoot",
            extra_roll=roll == 6,
            finished=False,
        )
        return _apply_turn_limit(new_state, result, turn_limit)

    if raw_position == FINISH_CELL:
        final_position = FINISH_CELL
        transition = None
    else:
        transition_lookup = board.resolve_transition(raw_position)
        if transition_lookup is None:
            final_position = raw_position
            transition = None
        else:
            final_position, transition = transition_lookup

    finished = final_position == FINISH_CELL
    event: MoveEvent = "finished" if finished else "moved"

    new_state = GameState(
        position=final_position,
        in_play=True,
        turn=next_turn,
        finished=finished,
        finish_reason="enlightenment" if finished else None,
        history=state.history
        + (
            MoveRecord(
                turn=next_turn,
                roll=roll,
                from_position=from_position,
                raw_position=raw_position,
                final_position=final_position,
                transition=transition,
                event=event,
            ),
        ),
    )
    result = MoveResult(
        turn=next_turn,
        roll=roll,
        from_position=from_position,
        raw_position=raw_position,
        final_position=final_position,
        transition=transition,
        event=event,
        extra_roll=(roll == 6) and not finished,
        finished=finished,
    )
    if finished:
        return new_state, result
    return _apply_turn_limit(new_state, result, turn_limit)


def _apply_turn_limit(
    state: GameState, result: MoveResult, turn_limit: int | None
) -> tuple[GameState, MoveResult]:
    if turn_limit is None or state.finished or state.turn < turn_limit:
        return state, result

    limited_state = GameState(
        position=state.position,
        in_play=state.in_play,
        turn=state.turn,
        finished=True,
        finish_reason="turn_limit",
        history=state.history,
    )
    limited_result = MoveResult(
        turn=result.turn,
        roll=result.roll,
        from_position=result.from_position,
        raw_position=result.raw_position,
        final_position=result.final_position,
        transition=result.transition,
        event="turn_limit_reached",
        extra_roll=False,
        finished=True,
    )
    return limited_state, limited_result


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
