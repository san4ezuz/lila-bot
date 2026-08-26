import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.board import Board, get_default_board
from src.core.models import GameState, MoveResult
from src.core.rules import apply_roll
from src.db.models import Game, User
from src.db.repositories import (
    EventRepository,
    GameRepository,
    MessageRepository,
    MoveRepository,
    UserRepository,
)
from src.llm import guide, safety


class GameNotFoundError(Exception):
    pass


class GameNotActiveError(Exception):
    pass


class PaymentRequiredError(Exception):
    def __init__(self, price: int, pay_url: str) -> None:
        self.price = price
        self.pay_url = pay_url
        super().__init__(f"payment required: {price}")


def _game_state(game: Game) -> GameState:
    """Reconstruct just enough GameState to feed apply_roll(). Full move history lives in the
    `moves` table (see MoveRepository) and isn't needed to compute the next roll — apply_roll()
    is pure and only depends on the current position/in_play/turn."""
    return GameState(position=game.position, in_play=game.in_play, turn=game.turn)


class GameService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.games = GameRepository(session)
        self.moves = MoveRepository(session)
        self.messages = MessageRepository(session)
        self.events = EventRepository(session)
        self.board: Board = get_default_board()
        self.settings = get_settings()

    async def start_game(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        intention: str | None,
    ) -> tuple[Game, str]:
        user = await self.users.get_or_create(telegram_id, username, first_name)

        active = await self.games.get_active_for_user(user.id)
        if active is not None:
            last_message = "У тебя уже есть партия в процессе — продолжим её."
            return active, last_message

        if not user.has_free_game_used:
            is_paid = False
        elif user.paid_games_available > 0:
            await self.users.consume_paid_game(user)
            is_paid = True
        else:
            await self.events.log("paywall_shown", user.id, None)
            # Tribute exposes no API to mint a per-user checkout link with custom metadata
            # (see src/services/billing_service.py docstring) — the pay link is a static one
            # configured once in Creator Dashboard; correlation back to this user happens via
            # the telegramId Tribute's webhook already carries (payment happens inside Telegram).
            raise PaymentRequiredError(
                price=self.settings.price_second_game_rub,
                pay_url=self.settings.tribute_pay_url,
            )

        game = await self.games.create(user.id, self.board.version, is_paid)
        await self.events.log("game_started", user.id, game.id, {"is_paid": is_paid})

        if intention:
            guide_message = await self.set_intention(game, intention)
        else:
            guide_message = guide.intro_message()
            await self.messages.create(game.id, "guide", guide_message)

        return game, guide_message

    async def set_intention(self, game: Game, intention: str) -> str:
        await self.games.set_intention(game, intention)
        guide_message = guide.intention_ack_message(intention)
        await self.messages.create(game.id, "guide", guide_message)
        await self.events.log("intention_set", game.user_id, game.id)
        return guide_message

    async def roll(
        self, game: Game
    ) -> tuple[MoveResult, str, bool]:
        """Returns (result, guide_message, can_roll)."""
        if game.status != "active":
            raise GameNotActiveError("game is not active")

        state = _game_state(game)
        roll_value = random.randint(1, 6)
        new_state, result = apply_roll(state, roll_value, self.board, self.settings.turn_limit)

        await self.games.apply_state(game, new_state)
        move = await self.moves.create(game.id, result)

        guide_message = await self._roll_guide_message(game, result)
        await self.messages.create(game.id, "guide", guide_message, move_id=move.id)
        await self.events.log(
            "move_made",
            game.user_id,
            game.id,
            {"turn": result.turn, "position": result.final_position, "transition": result.transition},
        )

        if game.status == "finished":
            await self._on_finished(game)

        can_roll = game.status == "active"
        return result, guide_message, can_roll

    async def _roll_guide_message(self, game: Game, result: MoveResult) -> str:
        if result.event == "not_entered":
            return guide.not_entered_message(result.roll)
        if result.event == "turn_limit_reached":
            return guide.turn_limit_message()
        if result.final_position is None:
            return guide.not_entered_message(result.roll)

        cell = self.board.get_cell(result.final_position)
        if result.event == "blocked_overshoot":
            return guide.blocked_overshoot_message(cell)

        # entered / moved / finished all land on a new cell — worth an LLM interpretation.
        visited = await self._visited_names(game.id)
        recent_answers = await self._recent_player_answers(game.id)
        return await guide.interpret_cell(
            intention=game.intention,
            cell=cell,
            event=result.event,
            transition=result.transition,
            visited=visited,
            recent_player_answers=recent_answers,
        )

    async def _visited_names(self, game_id: uuid.UUID) -> list[str]:
        moves = await self.moves.list_for_game(game_id)
        return [
            f"{m.to_pos} {self.board.get_cell(m.to_pos).name}"
            for m in moves
            if m.to_pos is not None
        ]

    async def _recent_player_answers(self, game_id: uuid.UUID) -> list[str]:
        messages = await self.messages.list_for_game(game_id)
        return [m.content for m in messages if m.role == "player"][-2:]

    async def skip_response(self, game: Game) -> None:
        if game.status != "active":
            raise GameNotActiveError("game is not active")
        await self.events.log("response_skipped", game.user_id, game.id)

    async def respond(self, game: Game, text: str) -> tuple[str, str | None]:
        if game.status != "active":
            raise GameNotActiveError("game is not active")

        await self.messages.create(game.id, "player", text)
        safety_flag = safety.screen(text)
        if safety_flag:
            guide_message = guide.safety_referral_message()
        elif game.position is not None:
            cell = self.board.get_cell(game.position)
            guide_message = await guide.deepen(game.intention, cell, text)
        else:
            # Player replied before their first roll landed them on a cell — nothing to deepen
            # yet; a static nudge back to the mechanic is enough (see docs/PLAN.md §8.1).
            guide_message = "Слышу тебя. Бросай кубик, чтобы войти в игру, и продолжим оттуда."
        await self.messages.create(game.id, "guide", guide_message)

        await self.events.log(
            "player_responded", game.user_id, game.id, {"char_len": len(text)}
        )
        if safety_flag:
            await self.events.log("safety_triggered", game.user_id, game.id, {"flag": safety_flag})

        return guide_message, safety_flag

    async def finish_game(self, game: Game) -> tuple[str, dict]:
        if game.status not in ("active", "finished"):
            raise GameNotActiveError("game cannot be finished from its current status")

        visited = await self._visited_names(game.id)
        summary = await guide.summarize_game(game.intention, visited)
        await self.games.set_summary(game, summary)

        if game.status != "finished":
            game.status = "finished"
            game.finished_at = datetime.now(UTC)
            game.finish_reason = game.finish_reason or "player_ended"
            await self.session.flush()
            await self._on_finished(game)

        stats = {"turns": game.turn, "finish_reason": game.finish_reason}
        await self.events.log("game_finished", game.user_id, game.id, stats)
        return summary, stats

    async def _on_finished(self, game: Game) -> None:
        if game.is_paid:
            return
        user = await self.session.get(User, game.user_id)
        if user is not None and not user.has_free_game_used:
            await self.users.mark_free_game_used(user)

    async def get_game(self, game_id: uuid.UUID) -> Game:
        game = await self.games.get(game_id)
        if game is None:
            raise GameNotFoundError(str(game_id))
        return game

    async def abandon_stale_games(self, hours: int = 48) -> int:
        """Marks games with no activity for `hours` as abandoned and logs `game_abandoned` per
        game (docs/PLAN.md §12) — meant to run on a schedule, see scripts/abandon_stale_games.py.
        Not wired into request handling: an abandoned game frees up `get_active_for_user()` for
        that user (they can just /start again), it isn't itself blocking anything."""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        stale = await self.games.find_stale_active(cutoff)
        for game in stale:
            await self.games.mark_abandoned(game)
            await self.events.log("game_abandoned", game.user_id, game.id, {"turn": game.turn})
        return len(stale)

    async def get_user_status(self, telegram_id: int) -> dict:
        user = await self.users.get_by_telegram_id(telegram_id)
        if user is None:
            return {
                "has_free_game_used": False,
                "paid_games_available": 0,
                "active_game_id": None,
            }
        active = await self.games.get_active_for_user(user.id)
        return {
            "has_free_game_used": user.has_free_game_used,
            "paid_games_available": user.paid_games_available,
            "active_game_id": active.id if active else None,
        }
