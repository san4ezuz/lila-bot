"""Game credits and everything that hands them out: purchases, refunds, /grant, referrals, gifts
and promo codes. Every change is a row in `credit_ledger` (src/db/models.py::CreditEntry); the
balance is their sum.

Reasons — grants: `paid` (Tribute purchase), `admin` (/grant), `referral` (an invited friend,
and the inviter once that friend finishes a game), `gift` (a gift link opened), `promo` (a promo
code), `legacy` (the old counter, moved over by migration e7b4a1c9d2f6). Spends: `game` (a game
started), `refund` (a refunded purchase takes its credit back), `gift_sent` (a gift link created).
The first free game is not a credit — see `users.has_free_game_used`.

Deep links (`t.me/<bot>?start=<payload>`) carry `ref_<code>`, `gift_<code>` or `promo_<CODE>`.
"""

import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Game, User
from src.db.repositories import (
    CreditRepository,
    EventRepository,
    GameRepository,
    GiftRepository,
    PromoCodeRepository,
    UserRepository,
)

# What an invite is worth: the friend gets a game on top of the free one at once; the inviter
# gets one when that friend finishes a game — playing it through, not ending it with /newgame,
# so a stack of throwaway accounts doesn't pay.
REFERRAL_FRIEND_CREDITS = 1
REFERRAL_INVITER_CREDITS = 1

_CODE_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no 0/o, 1/l — codes get retyped
_PROMO_PATTERN = re.compile(r"[A-Z0-9_-]{2,32}")

LinkKind = Literal["referral", "gift", "promo"]
LinkOutcome = Literal[
    "granted",
    "invalid",
    "own",  # the player's own invite or gift link
    "not_new",  # a referral link opened by someone who already plays or was already invited
    "already_used",  # this promo code by this player, or a gift someone else opened
    "exhausted",
    "expired",
]


@dataclass(slots=True)
class LinkResult:
    kind: LinkKind | None
    outcome: LinkOutcome | None = None
    credits: int = 0
    # Messages to send once the transaction is committed: (telegram_id, text).
    notifications: list[tuple[int, str]] = field(default_factory=list)


class NoCreditsError(Exception):
    pass


def _new_code(length: int) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def normalize_promo(code: str) -> str | None:
    code = code.strip().upper()
    return code if _PROMO_PATTERN.fullmatch(code) else None


REFERRAL_REWARD_TEXT = (
    "Партия по твоему приглашению дошла до финала. Тебе начислена ещё одна партия — "
    "/start, когда захочется."
)
GIFT_OPENED_TEXT = "Твой подарок открыли: партия в «Лилу» теперь у адресата. Спасибо, что делишься."


class CreditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.games = GameRepository(session)
        self.credits = CreditRepository(session)
        self.gifts = GiftRepository(session)
        self.promos = PromoCodeRepository(session)
        self.events = EventRepository(session)

    async def balance(self, user: User) -> int:
        return await self.credits.balance(user.id)

    async def grant(self, user: User, reason: str, ref: str | None = None, count: int = 1) -> bool:
        return await self.credits.add(user.id, count, reason, ref)

    async def spend_for_game(self, user: User, game_id: uuid.UUID) -> None:
        """The caller has locked the user (UserRepository.lock) and checked the balance."""
        await self.credits.add(user.id, -1, "game", game_id=game_id)

    async def take_back(self, user: User, reason: str, ref: str | None = None) -> bool:
        """Takes back one unused credit. Returns False if none is left — a refund must not push
        the balance negative when the game was already started."""
        await self.users.lock(user)
        if await self.credits.balance(user.id) <= 0:
            return False
        await self.credits.add(user.id, -1, reason, ref)
        return True

    # --- invites -----------------------------------------------------------------------------

    async def ref_code(self, user: User) -> str:
        if user.ref_code is None:
            code = _new_code(8)
            while await self.users.get_by_ref_code(code) is not None:
                code = _new_code(8)
            await self.users.set_ref_code(user, code)
        assert user.ref_code is not None
        return user.ref_code

    async def apply_start_link(self, telegram_id: int, payload: str) -> LinkResult:
        """Interprets a `/start` payload. Anything that isn't an invite, a gift or a promo code is
        a plain traffic source and returns kind=None."""
        prefix, _, value = payload.partition("_")
        if not value:
            return LinkResult(kind=None)
        user = await self.users.get_or_create(telegram_id)
        if prefix == "ref":
            return await self._apply_referral(user, value)
        if prefix == "gift":
            return await self.redeem_gift(user, value)
        if prefix == "promo":
            return await self.redeem_promo(user, value)
        return LinkResult(kind=None)

    async def _apply_referral(self, user: User, code: str) -> LinkResult:
        inviter = await self.users.get_by_ref_code(code.lower())
        if inviter is None:
            return LinkResult("referral", "invalid")
        if inviter.id == user.id:
            return LinkResult("referral", "own")
        # Only someone new to the game counts as invited: no referrer yet and no game played.
        if user.referred_by is not None or await self.games.count_for_user(user.id) > 0:
            return LinkResult("referral", "not_new")

        await self.users.set_referred_by(user, inviter.id)
        await self.credits.add(
            user.id, REFERRAL_FRIEND_CREDITS, "referral", ref=f"invited_by:{inviter.id}"
        )
        await self.events.log("referral_joined", user.id, None, {"inviter_id": inviter.id})
        return LinkResult("referral", "granted", REFERRAL_FRIEND_CREDITS)

    async def on_game_finished(self, game: Game) -> list[tuple[int, str]]:
        """Pays the inviter when the invited player finishes a game for the first time."""
        if game.finish_reason == "player_ended":
            return []
        user = await self.session.get(User, game.user_id)
        if user is None or user.referred_by is None:
            return []
        inviter = await self.session.get(User, user.referred_by)
        if inviter is None:
            return []
        granted = await self.credits.add(
            inviter.id, REFERRAL_INVITER_CREDITS, "referral", ref=f"friend:{user.id}"
        )
        if not granted:
            return []
        await self.events.log("referral_rewarded", inviter.id, game.id, {"friend_id": user.id})
        return [(inviter.telegram_id, REFERRAL_REWARD_TEXT)]

    # --- gifts -------------------------------------------------------------------------------

    async def create_gift(self, telegram_id: int) -> str:
        """Spends one of the giver's credits on a gift link. Raises NoCreditsError without one."""
        user = await self.users.get_or_create(telegram_id)
        await self.users.lock(user)
        if await self.credits.balance(user.id) <= 0:
            raise NoCreditsError
        code = _new_code(10)
        await self.gifts.create(code, user.id)
        await self.credits.add(user.id, -1, "gift_sent", ref=code)
        await self.events.log("gift_created", user.id, None, {"code": code})
        return code

    async def redeem_gift(self, user: User, code: str) -> LinkResult:
        gift = await self.gifts.get_for_update(code.lower())
        if gift is None:
            return LinkResult("gift", "invalid")
        if gift.from_user_id == user.id:
            return LinkResult("gift", "own")
        if gift.redeemed_by is not None:
            # Opening the same link again shows the same "it's yours" rather than an error.
            outcome: LinkOutcome = "granted" if gift.redeemed_by == user.id else "already_used"
            return LinkResult("gift", outcome, 1 if outcome == "granted" else 0)

        await self.gifts.redeem(gift, user.id)
        await self.credits.add(user.id, 1, "gift", ref=gift.code)
        await self.events.log("gift_redeemed", user.id, None, {"code": gift.code})
        giver = await self.session.get(User, gift.from_user_id)
        notifications = [(giver.telegram_id, GIFT_OPENED_TEXT)] if giver is not None else []
        return LinkResult("gift", "granted", 1, notifications)

    # --- promo codes -------------------------------------------------------------------------

    async def redeem_promo(self, user: User, raw_code: str) -> LinkResult:
        code = normalize_promo(raw_code)
        promo = await self.promos.get_for_update(code) if code is not None else None
        if promo is None or not promo.is_active:
            return LinkResult("promo", "invalid")
        if await self.credits.count_grants("promo", promo.code, user_id=user.id):
            return LinkResult("promo", "already_used")
        if promo.expires_at is not None and promo.expires_at <= datetime.now(UTC):
            return LinkResult("promo", "expired")
        uses = await self.credits.count_grants("promo", promo.code)
        if promo.max_uses is not None and uses >= promo.max_uses:
            return LinkResult("promo", "exhausted")
        # The row lock on the promo code keeps the limit exact; the unique index — one use each.
        if not await self.credits.add(user.id, promo.credits, "promo", ref=promo.code):
            return LinkResult("promo", "already_used")
        await self.events.log(
            "promo_redeemed", user.id, None, {"code": promo.code, "credits": promo.credits}
        )
        return LinkResult("promo", "granted", promo.credits)

    async def create_promo(
        self, raw_code: str, credits: int, max_uses: int | None, note: str | None
    ) -> str:
        code = normalize_promo(raw_code)
        if code is None or credits < 1 or (max_uses is not None and max_uses < 1):
            raise ValueError("bad promo code")
        await self.promos.upsert(code, credits, max_uses, note)
        return code
