from src.core.board import BOARD_MAX_CELL, ENTRY_CELL, FINISH_CELL, Board
from src.core.models import GameState, MoveRecord, MoveResult

DEFAULT_TURN_LIMIT = 60


def create_initial_state() -> GameState:
    return GameState()


def is_finished(state: GameState) -> bool:
    return state.finished


def apply_roll(
    state: GameState,
    roll: int,
    board: Board,
    turn_limit: int = DEFAULT_TURN_LIMIT,
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
        if roll == ENTRY_CELL:
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
    event = "finished" if finished else "moved"

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
    state: GameState, result: MoveResult, turn_limit: int
) -> tuple[GameState, MoveResult]:
    if state.finished or state.turn < turn_limit:
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
