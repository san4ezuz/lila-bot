from aiogram.fsm.state import State, StatesGroup


class GameStates(StatesGroup):
    """FSM is for UI steps only — never for game state, which lives entirely behind the API
    (docs/PLAN.md §6.2). The only step that needs one is "the next text message is the
    player's intention, not a reply to a cell"."""

    waiting_intention = State()
    waiting_feedback = State()
