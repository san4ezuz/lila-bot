from dataclasses import dataclass
from typing import Literal

TransitionType = Literal["arrow", "snake"]

MoveEvent = Literal[
    "not_entered",
    "entered",
    "moved",
    "blocked_overshoot",
    "finished",
    "turn_limit_reached",
]


@dataclass(frozen=True, slots=True)
class MoveResult:
    """Outcome of applying a single dice roll, returned to the caller of apply_roll()."""

    turn: int
    roll: int
    from_position: int | None
    raw_position: int | None
    final_position: int | None
    transition: TransitionType | None
    event: MoveEvent
    extra_roll: bool
    finished: bool


@dataclass(frozen=True, slots=True)
class GameState:
    """Immutable snapshot of a game's position. apply_roll() returns a new instance."""

    position: int | None = None
    in_play: bool = False
    turn: int = 0
    finished: bool = False
    finish_reason: Literal["enlightenment", "turn_limit"] | None = None
