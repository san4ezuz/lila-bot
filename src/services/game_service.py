import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.board import ENTRY_CELL, Board, get_default_board
from src.core.dice import ENTRY_GUARANTEE_ATTEMPTS, roll_entry_die
from src.core.models import GameState, MoveEvent, MoveResult, TransitionType
from src.core.rules import ASSIST_FROM_TURN, apply_roll, shortest_path_rolls
from src.db.models import Game, Move, User
from src.db.repositories import (
    EventRepository,
    GameRepository,
    LlmCallRepository,
    MessageRepository,
    MoveRepository,
    UserRepository,
)
from src.llm import guide, safety
from src.llm.client import LlmResult, skipped_result
from src.services.alerts import note_llm_result
from src.services.crisis import in_crisis_mode
from src.services.llm_limits import LlmLimits


class GameNotFoundError(Exception):
    pass


class GameNotActiveError(Exception):
    pass


# A game auto-marked 'abandoned' by scripts/abandon_stale_games.py (48h inactivity) is not dead —
# reminders keep nudging the player up to 7 days (docs/UX_ITERATION_1.md §2), and a returning
# player must be able to pick it back up. "Resumable" = the player can still act on it.
_RESUMABLE_STATUSES = ("active", "abandoned")

# What the client should do when the crisis screen fires: show a supportive text with help
# contacts instead of the usual reply.
SAFETY_FLAG = "soft_referral"


class PaymentRequiredError(Exception):
    """`crisis` — the player is in crisis mode (src/services/crisis.py): the client shows help
    contacts instead of a pay button."""

    def __init__(self, price: int, pay_url: str, crisis: bool = False) -> None:
        self.price = price
        self.pay_url = pay_url
        self.crisis = crisis
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
        self.llm_calls = LlmCallRepository(session)
        self.limits = LlmLimits(session)
        self.board: Board = get_default_board()
        self.settings = get_settings()
        self.rng = random.Random()

    async def start_game(self, telegram_id: int, intention: str | None) -> tuple[Game, str]:
        # Only the Telegram id is stored — no name or username (see texts.about_game_text).
        user = await self.users.get_or_create(telegram_id)

        active = await self.games.get_active_for_user(user.id)
        if active is not None:
            last_message = "У тебя уже есть партия в процессе — продолжим её."
            return active, last_message

        if not user.has_free_game_used:
            is_paid = False
        elif user.paid_games_available > 0:
            await self.users.consume_paid_game(user)
            is_paid = True
        elif in_crisis_mode(user):
            await self.events.log("paywall_suppressed", user.id, None, {"reason": "crisis"})
            raise PaymentRequiredError(
                price=self.settings.price_second_game_rub,
                pay_url=self.settings.tribute_pay_url,
                crisis=True,
            )
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
            guide_message, _ = await self.set_intention(game, intention)
        else:
            guide_message = guide.intro_message()
            await self.messages.create(game.id, "guide", guide_message)

        return game, guide_message

    async def set_intention(self, game: Game, intention: str) -> tuple[str, str | None]:
        """Returns (guide_message, safety_flag). A request that trips the crisis screen is not
        saved: the game stays in 'setup', and the guide would otherwise quote it back on later
        cells («ты пришёл с запросом …»)."""
        await self.session.commit()  # the classifier is an LLM call
        source, classifier_call = await safety.check(intention)
        await self._record_llm(game, classifier_call)
        if source is not None:
            await self._on_safety_trigger(game, source, "intention")
            guide_message = guide.safety_referral_message(asks_intention=True)
            await self.messages.create(game.id, "guide", guide_message)
            return guide_message, SAFETY_FLAG

        await self.games.set_intention(game, intention)
        await self.events.log("intention_set", game.user_id, game.id)

        # Entry requires rolling a 6 (docs/RULES.md §2) — the player's next roll is their first
        # entry attempt, so nothing moves yet here.
        gender = await self._user_gender(game.user_id)
        guide_message = guide.intention_ack_message(intention, gender)
        await self.messages.create(game.id, "guide", guide_message)
        return guide_message, None

    async def roll(
        self, game: Game, idempotency_key: str | None = None
    ) -> tuple[MoveResult, str, bool]:
        """Returns (result, guide_message, can_roll). The row lock serializes concurrent rolls on
        one game; a repeat of `idempotency_key` replays the move it made instead of rolling again.
        The move is committed before the LLM call, so a failure there can't lose it and no DB
        connection or lock is held while waiting on the model."""
        await self.session.refresh(game, with_for_update=True)
        if idempotency_key is not None:
            existing = await self.moves.get_by_idempotency_key(game.id, idempotency_key)
            if existing is not None:
                return await self._replay_roll(game, existing)

        if game.status not in _RESUMABLE_STATUSES:
            raise GameNotActiveError("game is not active")
        if game.status == "abandoned":
            game.status = "active"

        state = _game_state(game)
        roll_value = self._pick_roll(game)
        new_state, result = apply_roll(state, roll_value, self.board, self.settings.turn_limit)

        await self.games.apply_state(game, new_state)
        move = await self.moves.create(game.id, result, idempotency_key)
        await self.events.log(
            "move_made",
            game.user_id,
            game.id,
            {"turn": result.turn, "position": result.final_position, "transition": result.transition},
        )
        if game.status == "finished":
            await self._on_finished(game)
        await self.session.commit()

        guide_message = await self._roll_guide_message(game, result)
        await self.messages.create(game.id, "guide", guide_message, move_id=move.id)

        can_roll = game.status == "active"
        return result, guide_message, can_roll

    async def _replay_roll(self, game: Game, move: Move) -> tuple[MoveResult, str, bool]:
        """Rebuilds the response for a roll that already happened. The guide message can be missing
        if the first attempt died between committing the move and the LLM reply — regenerate it
        then, so a retry still gets an answer."""
        finished = move.event in ("finished", "turn_limit_reached")
        extra_roll = move.event == "entered" or (
            move.roll == 6 and move.event in ("moved", "blocked_overshoot")
        )
        result = MoveResult(
            turn=move.turn,
            roll=move.roll,
            from_position=move.from_pos,
            raw_position=move.raw_pos,
            final_position=move.to_pos,
            transition=cast(TransitionType | None, move.transition),
            event=cast(MoveEvent, move.event),
            extra_roll=extra_roll,
            finished=finished,
        )
        message = await self.messages.get_for_move(move.id)
        if message is not None:
            guide_message = message.content
        else:
            guide_message = await self._roll_guide_message(game, result)
            await self.messages.create(game.id, "guide", guide_message, move_id=move.id)
        return result, guide_message, game.status == "active"

    def _pick_roll(self, game: Game) -> int:
        """Picks the die value for this roll. Before entry, uses the weighted entry die
        (docs/RULES.md §2 — climbing odds of a natural six, guaranteed by attempt
        ENTRY_GUARANTEE_ATTEMPTS). Once in play and past ASSIST_FROM_TURN, steers the roll along
        the shortest remaining path to the finish instead of rolling truly at random, so the
        player reliably reaches cell 68 before self.settings.turn_limit (docs/RULES.md §5)."""
        if not game.in_play:
            return roll_entry_die(game.turn + 1, self.rng)

        if (
            self.settings.turn_limit is not None
            and game.turn >= ASSIST_FROM_TURN
            and game.position is not None
        ):
            forced_path = shortest_path_rolls(game.position, self.board)
            if forced_path:
                return forced_path[0]

        return self.rng.randint(1, 6)

    async def _roll_guide_message(self, game: Game, result: MoveResult) -> str:
        gender = await self._user_gender(game.user_id)

        if result.event == "not_entered" or result.final_position is None:
            use_llm = await self.limits.allows_call(game.user_id, game.id)
            await self.session.commit()  # no idle-in-transaction connection during the LLM call
            reply = await guide.not_entered_message(
                game.intention,
                result.roll,
                result.turn,
                ENTRY_GUARANTEE_ATTEMPTS,
                gender,
                use_llm=use_llm,
            )
            await self._record_llm(game, reply)
            return reply.text
        if result.event == "turn_limit_reached":
            return guide.turn_limit_message()

        cell = self.board.get_cell(result.final_position)
        if result.event == "blocked_overshoot":
            message = guide.blocked_overshoot_message(cell)
        elif result.event == "entered" and result.roll == ENTRY_CELL and result.turn == 1:
            # Natural six on the very first attempt — everyone starts here, so celebrate the
            # request instead of a deep interpretation of "Заблуждение" that adds nothing yet.
            message = guide.first_try_entry_message(game.intention, gender)
        else:
            # entered (later/forced) / moved / finished all land on a new cell — worth an LLM
            # interpretation. On a snake/arrow transition, also hand over the source cell the
            # player was swept past without ever landing on, so the guide can name it.
            visited = await self._visited_names(game.id)
            recent_answers = await self._recent_player_answers(game.id)
            skipped_cell = (
                self.board.get_cell(result.raw_position)
                if result.transition is not None and result.raw_position is not None
                else None
            )
            use_llm = await self.limits.allows_call(game.user_id, game.id)
            await self.session.commit()
            cell_reply = await guide.interpret_cell(
                intention=game.intention,
                cell=cell,
                event=result.event,
                transition=result.transition,
                skipped_cell=skipped_cell,
                visited=visited,
                recent_player_answers=recent_answers,
                gender=gender,
                use_llm=use_llm,
            )
            await self._record_llm(game, cell_reply)
            cell_message = cell_reply.text
            # Entry guaranteed by ENTRY_GUARANTEE_ATTEMPTS fires without an actual 6 — say so
            # before the LLM's interpretation of the cell itself.
            if result.event == "entered" and result.roll != ENTRY_CELL:
                message = f"{guide.forced_entry_message()}\n\n{cell_message}"
            else:
                message = cell_message

        # Rolling a 6 always grants another roll (docs/RULES.md §3) — explain the mechanic
        # rather than silently handing back the roll button again.
        if result.roll == 6 and result.extra_roll:
            message = f"{message}\n\n{guide.six_extra_roll_note()}"
        return message

    async def _record_llm(self, game: Game, *results: LlmResult | None) -> None:
        for result in results:
            if result is None:
                continue
            await self.llm_calls.record(result, game.user_id, game.id)
            note_llm_result(result)

    async def _user_gender(self, user_id: int) -> str | None:
        user = await self.session.get(User, user_id)
        return user.gender if user else None

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
        if game.status not in _RESUMABLE_STATUSES:
            raise GameNotActiveError("game is not active")
        if game.status == "abandoned":
            game.status = "active"
            await self.session.flush()
        await self.events.log("response_skipped", game.user_id, game.id)

    async def respond(self, game: Game, text: str) -> tuple[str, str | None]:
        if game.status not in _RESUMABLE_STATUSES:
            raise GameNotActiveError("game is not active")
        if game.status == "abandoned":
            game.status = "active"
            await self.session.flush()

        await self.messages.create(game.id, "player", text)
        await self.events.log(
            "player_responded", game.user_id, game.id, {"char_len": len(text)}
        )

        source: safety.SafetySource | None = "lexical" if safety.screen(text) else None
        guide_message = ""
        if source is None:
            cell_reply_allowed = await self.limits.allows_cell_reply(game.user_id, game.id)
            use_llm = cell_reply_allowed and await self.limits.allows_call(game.user_id, game.id)
            # The classifier runs alongside the reply so it adds no latency; its verdict wins.
            await self.session.commit()
            classifier_call, reply = await asyncio.gather(
                safety.classify(text),
                self._deepen_reply(game, text, use_llm, cell_reply_allowed),
            )
            await self._record_llm(game, classifier_call, reply)
            guide_message = reply.text
            if safety.is_risk(classifier_call):
                source = "classifier"

        if source is not None:
            await self._on_safety_trigger(game, source, "response")
            guide_message = guide.safety_referral_message()
        await self.messages.create(game.id, "guide", guide_message)
        return guide_message, SAFETY_FLAG if source is not None else None

    async def _deepen_reply(
        self, game: Game, text: str, use_llm: bool, cell_reply_allowed: bool
    ) -> LlmResult:
        if game.position is None:
            # Player replied before their first roll landed them on a cell — nothing to deepen
            # yet; a static nudge back to the mechanic is enough (see docs/PLAN.md §8.1).
            return skipped_result(
                "deepen", "Слышу тебя. Бросай кубик, чтобы войти в игру, и продолжим оттуда."
            )
        if not cell_reply_allowed:
            return skipped_result("deepen", guide.cell_reply_limit_message())
        cell = self.board.get_cell(game.position)
        gender = await self._user_gender(game.user_id)
        await self.session.commit()  # no idle-in-transaction connection during the LLM call
        return await guide.deepen(game.intention, cell, text, gender, use_llm=use_llm)

    async def _on_safety_trigger(self, game: Game, source: str, where: str) -> None:
        user = await self.session.get(User, game.user_id)
        if user is not None:
            await self.users.mark_crisis(user)
        await self.events.log(
            "safety_triggered",
            game.user_id,
            game.id,
            {"flag": SAFETY_FLAG, "source": source, "where": where},
        )

    async def is_in_crisis(self, user_id: int) -> bool:
        user = await self.session.get(User, user_id)
        return user is not None and in_crisis_mode(user)

    async def finish_game(self, game: Game) -> tuple[str, dict]:
        if game.status not in (*_RESUMABLE_STATUSES, "finished"):
            raise GameNotActiveError("game cannot be finished from its current status")

        # Repeat call (a retry, or /newgame right after the finish) — the player has this summary
        # already; don't pay for a new one or count the finish twice.
        if game.status == "finished" and game.summary:
            return game.summary, {"turns": game.turn, "finish_reason": game.finish_reason}

        visited = await self._visited_names(game.id)
        gender = await self._user_gender(game.user_id)
        use_llm = await self.limits.allows_call(game.user_id, game.id)
        await self.session.commit()
        reply = await guide.summarize_game(game.intention, visited, gender, use_llm=use_llm)
        await self._record_llm(game, reply)
        summary = reply.text
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

    async def record_feedback(self, game: Game, text: str) -> str | None:
        """Captures the answer to the finish-time feedback question as a structured event —
        docs/PLAN.md §12's `feedback_given`. Deliberately not gated on game.status: the
        conversational prompt happens right after finish, so the game is already 'finished' by
        the time the player replies. Returns the supportive text if the answer trips the crisis
        screen, else None."""
        await self.events.log(
            "feedback_given", game.user_id, game.id, {"text": text[:500]}
        )
        await self.session.commit()  # the classifier is an LLM call
        source, classifier_call = await safety.check(text)
        await self._record_llm(game, classifier_call)
        if source is None:
            return None
        await self._on_safety_trigger(game, source, "feedback")
        return guide.safety_referral_message()

    async def abandon_stale_games(self, hours: int = 48) -> int:
        """Marks games with no activity for `hours` as abandoned and logs `game_abandoned` per
        game (docs/PLAN.md §12) — meant to run on a schedule, see scripts/abandon_stale_games.py.
        Not wired into request handling: an abandoned game frees up `get_active_for_user()` for
        that user (they can just /start again), it isn't itself blocking anything."""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        stale = await self.games.find_stale_active(cutoff)
        for game in stale:
            await self.games.mark_abandoned(game)
            await self.events.log(
                "game_abandoned",
                game.user_id,
                game.id,
                {"turn": game.turn, "position": game.position, "in_play": game.in_play},
            )
        return len(stale)

    async def get_user_status(self, telegram_id: int) -> dict:
        user = await self.users.get_by_telegram_id(telegram_id)
        if user is None:
            return {
                "has_free_game_used": False,
                "paid_games_available": 0,
                "active_game_id": None,
                "active_game_status": None,
                "gender": None,
            }
        active = await self.games.get_active_for_user(user.id)
        return {
            "has_free_game_used": user.has_free_game_used,
            "paid_games_available": user.paid_games_available,
            "active_game_id": active.id if active else None,
            "active_game_status": active.status if active else None,
            "gender": user.gender,
        }

    async def set_gender(self, telegram_id: int, gender: str) -> None:
        user = await self.users.get_or_create(telegram_id)
        await self.users.set_gender(user, gender)
