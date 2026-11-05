"""Data for a finished game's share card (the bot draws it: src/bot/share_card.py) and the
Telegram file_ids of cards already sent, which inline mode offers in any chat.

The card is shareable, so it carries nothing the player wrote unless asked: the path, the key cell
and a line from the board about it. The request is printed only on the player's request.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import CardDataResponse, CardJump, CellOut, ShareCardOut, UserCardsResponse
from src.core.board import FINISH_CELL, FIRST_CELL, get_default_board
from src.db.models import Event, Game, Move, User
from src.db.repositories import (
    EventRepository,
    MoveRepository,
    ShareCardRepository,
    UserRepository,
)
from src.services.credit_service import CreditService
from src.services.game_service import GameNotActiveError

_LANDING_EVENTS = ("entered", "moved", "finished")
_INLINE_CARDS = 6


def key_cell(moves: list[Move], reactions: list[Event]) -> int:
    """The cell the card is about: the last one the player marked «Откликается»; else the longest
    arrow, else the longest snake; else where the game ended."""
    marked = [
        e.props["position"]
        for e in reactions
        if e.props.get("reaction") == "yes" and e.props.get("position") is not None
    ]
    if marked:
        return int(marked[-1])
    jumps = [m for m in moves if m.transition is not None and m.raw_pos and m.to_pos]
    for kind in ("arrow", "snake"):
        of_kind = [m for m in jumps if m.transition == kind]
        if of_kind:
            longest = max(of_kind, key=lambda m: abs(m.to_pos - m.raw_pos))  # type: ignore[operator]
            return int(longest.raw_pos)  # type: ignore[arg-type]
    landed = [m.to_pos for m in moves if m.event in _LANDING_EVENTS and m.to_pos is not None]
    return landed[-1] if landed else FIRST_CELL


def card_path(moves: list[Move]) -> tuple[list[int], list[CardJump]]:
    """Cells in the order the piece passed them; a snake or an arrow adds its start cell too."""
    path: list[int] = []
    jumps: list[CardJump] = []
    for m in moves:
        if m.event not in _LANDING_EVENTS or m.to_pos is None:
            continue
        if m.transition in ("arrow", "snake") and m.raw_pos is not None:
            path.append(m.raw_pos)
            jumps.append(CardJump(from_cell=m.raw_pos, to_cell=m.to_pos, kind=m.transition))  # type: ignore[arg-type]
        path.append(m.to_pos)
    return path, jumps


class CardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.moves = MoveRepository(session)
        self.events = EventRepository(session)
        self.cards = ShareCardRepository(session)
        self.credits = CreditService(session)
        self.board = get_default_board()

    async def card_data(self, game: Game) -> CardDataResponse:
        if game.status != "finished":
            raise GameNotActiveError("the card is made for a finished game")
        moves = await self.moves.list_for_game(game.id)
        reactions = await self.events.list_for_game(game.id, "cell_reaction")
        path, jumps = card_path(moves)
        cell = self.board.get_cell(key_cell(moves, reactions))
        user = await self.session.get(User, game.user_id)
        assert user is not None
        return CardDataResponse(
            game_id=game.id,
            path=path,
            jumps=jumps,
            key_cell=CellOut(id=cell.id, name=cell.name, theme=cell.theme),
            insight=cell.theme,
            question=cell.questions[0] if cell.questions else None,
            intention=game.intention,
            turns=game.turn,
            reached_finish=game.position == FINISH_CELL,
            ref_code=await self.credits.ref_code(user),
        )

    async def save_card(
        self, game: Game, format: str, with_intention: bool, file_id: str
    ) -> None:
        await self.cards.save(game.id, game.user_id, format, with_intention, file_id)
        await self.events.log(
            "card_created", game.user_id, game.id, {"format": format, "with_intention": with_intention}
        )

    async def user_cards(self, telegram_id: int) -> UserCardsResponse:
        user = await self.users.get_or_create(telegram_id)
        cards = await self.cards.recent_for_user(user.id, _INLINE_CARDS)
        return UserCardsResponse(
            cards=[
                ShareCardOut(file_id=c.file_id, format=c.format, with_intention=c.with_intention)  # type: ignore[arg-type]
                for c in cards
            ],
            ref_code=await self.credits.ref_code(user),
        )

