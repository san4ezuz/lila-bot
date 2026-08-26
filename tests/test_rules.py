import pytest

from src.core.board import get_default_board
from src.core.models import GameState
from src.core.rules import apply_roll, create_initial_state

BOARD = get_default_board()


def test_initial_state_is_not_in_play():
    state = create_initial_state()
    assert state.position is None
    assert state.in_play is False
    assert state.turn == 0
    assert state.finished is False


@pytest.mark.parametrize("roll", [1, 2, 3, 4, 5])
def test_roll_below_six_before_entry_does_not_enter(roll):
    state = create_initial_state()
    new_state, result = apply_roll(state, roll, BOARD)

    assert new_state.in_play is False
    assert new_state.position is None
    assert result.event == "not_entered"
    assert result.extra_roll is False
    assert new_state.turn == 1


def test_roll_of_six_before_entry_enters_the_game():
    state = create_initial_state()
    new_state, result = apply_roll(state, 6, BOARD)

    assert new_state.in_play is True
    assert new_state.position == 6
    assert result.event == "entered"
    assert result.extra_roll is True


def test_normal_move_without_transition():
    state = GameState(position=6, in_play=True, turn=1)
    # 6 + 3 = 9 ("Чувственный план"): a snake *target* (44->9) but not a transition source.
    new_state, result = apply_roll(state, 3, BOARD)

    assert new_state.position == 9
    assert result.event == "moved"
    assert result.transition is None


def test_landing_on_arrow_source_jumps_to_target():
    # cell 20 ("Благотворительность") is an arrow source -> 32 ("План равновесия").
    state = GameState(position=17, in_play=True, turn=5)
    new_state, result = apply_roll(state, 3, BOARD)

    assert result.raw_position == 20
    assert result.final_position == 32
    assert result.transition == "arrow"
    assert new_state.position == 32
    assert new_state.finished is False


def test_landing_on_snake_source_drops_to_target():
    # cell 12 ("Зависть") is a snake source -> 8 ("Алчность").
    state = GameState(position=9, in_play=True, turn=5)
    new_state, result = apply_roll(state, 3, BOARD)

    assert result.raw_position == 12
    assert result.final_position == 8
    assert result.transition == "snake"
    assert new_state.position == 8


def test_overshoot_past_the_physical_board_blocks_the_move():
    # board max is 72 (see docs/RULES.md §1, §5a) — 67 + 6 = 73 is beyond it.
    state = GameState(position=67, in_play=True, turn=10)
    new_state, result = apply_roll(state, 6, BOARD)

    assert result.event == "blocked_overshoot"
    assert new_state.position == 67
    assert new_state.finished is False


def test_overshooting_the_finish_but_not_the_board_enters_the_overshoot_zone():
    # 65 + 4 = 69: past the finish (68) but within the board (<=72) — not blocked,
    # see docs/RULES.md §5a. Cell 69 is not a transition source, so the token stays there.
    state = GameState(position=65, in_play=True, turn=10)
    new_state, result = apply_roll(state, 4, BOARD)

    assert result.event == "moved"
    assert result.final_position == 69
    assert new_state.finished is False


def test_exact_landing_on_finish_cell_finishes_the_game():
    state = GameState(position=65, in_play=True, turn=10)
    new_state, result = apply_roll(state, 3, BOARD)

    assert result.final_position == 68
    assert result.event == "finished"
    assert result.finished is True
    assert new_state.finished is True
    assert new_state.finish_reason == "enlightenment"


def test_landing_on_arrow_that_targets_the_finish_cell_also_finishes():
    # cell 54 ("План духовной преданности") is an arrow source -> 68 (finish).
    state = GameState(position=51, in_play=True, turn=10)
    new_state, result = apply_roll(state, 3, BOARD)

    assert result.raw_position == 54
    assert result.final_position == 68
    assert result.finished is True
    assert new_state.finish_reason == "enlightenment"


def test_overshoot_zone_snake_sends_the_token_back_below_the_finish_line():
    # cell 71 ("Раджогуна") + roll 1 = 72 ("Тамогуна"), which snakes down to 51 ("Земля").
    state = GameState(position=71, in_play=True, turn=15)
    new_state, result = apply_roll(state, 1, BOARD)

    assert result.raw_position == 72
    assert result.final_position == 51
    assert result.transition == "snake"
    assert new_state.finished is False


def test_turn_limit_forces_a_soft_finish():
    state = GameState(position=6, in_play=True, turn=1)
    new_state, result = apply_roll(state, 3, BOARD, turn_limit=2)

    assert new_state.turn == 2
    assert new_state.finished is True
    assert new_state.finish_reason == "turn_limit"
    assert result.event == "turn_limit_reached"


def test_cannot_roll_on_an_already_finished_game():
    state = GameState(position=68, in_play=True, turn=20, finished=True, finish_reason="enlightenment")
    with pytest.raises(ValueError):
        apply_roll(state, 4, BOARD)


def test_history_accumulates_one_record_per_roll():
    state = create_initial_state()
    state, _ = apply_roll(state, 2, BOARD)
    state, _ = apply_roll(state, 6, BOARD)
    state, _ = apply_roll(state, 4, BOARD)

    assert len(state.history) == 3
    assert [m.turn for m in state.history] == [1, 2, 3]
