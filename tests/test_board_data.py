from src.core.board import BOARD_MAX_CELL, FIRST_CELL, load_board


def test_board_has_72_cells_1_to_72_no_gaps_no_dupes():
    board = load_board()
    ids = sorted(board.cells.keys())
    assert ids == list(range(1, 73))


def test_every_cell_has_required_content():
    board = load_board()
    for cell in board.cells.values():
        assert cell.theme.strip(), f"cell {cell.id} has empty theme"
        assert len(cell.facets) >= 2, f"cell {cell.id} has fewer than 2 facets"
        assert len(cell.questions) >= 2, f"cell {cell.id} has fewer than 2 questions"
        assert cell.guna in ("tamas", "rajas", "sattva")


def test_verified_transition_count():
    """docs/leela_cells_final.json documents exactly 19 connections (9 arrows + 10 snakes),
    matching the marker count on the source board photo — a change here should be deliberate."""
    board = load_board()
    assert len(board.arrows) == 9
    assert len(board.snakes) == 10


def test_all_transition_endpoints_within_board_range():
    board = load_board()
    for source, target in {**board.arrows, **board.snakes}.items():
        assert FIRST_CELL <= source <= BOARD_MAX_CELL, f"transition source {source} out of range"
        assert FIRST_CELL <= target <= BOARD_MAX_CELL, f"transition target {target} out of range"


def test_arrows_point_upward():
    board = load_board()
    for source, target in board.arrows.items():
        assert target > source, f"arrow {source}->{target} does not point upward"


def test_snakes_point_downward():
    board = load_board()
    for source, target in board.snakes.items():
        assert target < source, f"snake {source}->{target} does not point downward"


def test_no_cell_is_both_arrow_and_snake_source():
    board = load_board()
    overlap = set(board.arrows) & set(board.snakes)
    assert not overlap, f"cells are both arrow and snake sources: {overlap}"


def test_transition_targets_are_never_transition_sources():
    """A cell you land on via a transition must not itself trigger another transition —
    apply_roll() only resolves one hop, so a chained transition would silently be dropped.
    Holds incidentally for the verified data; guards against a future data edit breaking it."""
    board = load_board()
    sources = set(board.arrows) | set(board.snakes)
    targets = set(board.arrows.values()) | set(board.snakes.values())
    chained = sources & targets
    assert not chained, f"transition targets that are also sources (would chain): {chained}"


def test_finish_cell_has_no_outgoing_transition():
    board = load_board()
    assert 68 not in board.arrows
    assert 68 not in board.snakes


def test_overshoot_zone_snake_leads_back_below_the_finish_line():
    """Cell 72 ('Тамогуна') is the only way out of the overshoot zone (69-72, see
    docs/RULES.md §5a) besides crawling forward one small roll at a time."""
    board = load_board()
    assert board.snakes[72] == 51
