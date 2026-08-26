import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
BOARD_PATH = DATA_DIR / "board.json"
TRANSITIONS_PATH = DATA_DIR / "transitions.json"

FIRST_CELL = 1
BOARD_MAX_CELL = 72  # physical extent of the board; movement past it is blocked
FINISH_CELL = 68  # exact landing here wins the game — see docs/RULES.md §4-5a
ENTRY_CELL = 6


@dataclass(frozen=True, slots=True)
class Cell:
    id: int
    name: str
    guna: Literal["tamas", "rajas", "sattva"]
    element: str
    plane: int
    theme: str
    keywords: tuple[str, ...]
    facets: tuple[str, ...]
    questions: tuple[str, ...]
    sanskrit: str | None = None


@dataclass(frozen=True, slots=True)
class Board:
    version: str
    cells: dict[int, Cell]
    arrows: dict[int, int]
    snakes: dict[int, int]

    def get_cell(self, cell_id: int) -> Cell:
        return self.cells[cell_id]

    def resolve_transition(self, cell_id: int) -> tuple[int, Literal["arrow", "snake"]] | None:
        if cell_id in self.arrows:
            return self.arrows[cell_id], "arrow"
        if cell_id in self.snakes:
            return self.snakes[cell_id], "snake"
        return None


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_board(board_path: Path = BOARD_PATH, transitions_path: Path = TRANSITIONS_PATH) -> Board:
    board_raw = _load_json(board_path)
    transitions_raw = _load_json(transitions_path)

    cells: dict[int, Cell] = {}
    for raw in board_raw["cells"]:
        cell = Cell(
            id=raw["id"],
            name=raw["name"],
            guna=raw["guna"],
            element=raw["element"],
            plane=raw["plane"],
            theme=raw["theme"],
            keywords=tuple(raw["keywords"]),
            facets=tuple(raw["facets"]),
            questions=tuple(raw["questions"]),
            sanskrit=raw.get("sanskrit"),
        )
        cells[cell.id] = cell

    arrows = {int(k): v for k, v in transitions_raw["arrows"].items()}
    snakes = {int(k): v for k, v in transitions_raw["snakes"].items()}

    return Board(version=board_raw["version"], cells=cells, arrows=arrows, snakes=snakes)


@lru_cache
def get_default_board() -> Board:
    return load_board()
