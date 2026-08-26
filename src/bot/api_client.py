"""Thin HTTP client the bot uses to talk to the Game API. This is the ONLY way handler code may
touch game state — no game logic, no DB, no LLM calls live in src/bot/ (docs/PLAN.md's one
architectural rule that must not be broken)."""

from typing import Any

import httpx

from src.api.schemas import (
    AdminGrantResponse,
    FinishGameResponse,
    GameStateResponse,
    RespondResponse,
    RollResponse,
    SetIntentionResponse,
    StartGameResponse,
    UserStatusResponse,
)
from src.config import get_settings


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message
        super().__init__(f"{status_code} {error}: {message}")


class PaymentRequiredError(Exception):
    def __init__(self, price: int, pay_url: str) -> None:
        self.price = price
        self.pay_url = pay_url
        super().__init__(f"payment required: {price}")


class GameApiClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.api_base_url,
            headers={"X-Internal-Token": settings.api_internal_token},
            timeout=45.0,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code == 402:
            body = response.json()
            raise PaymentRequiredError(price=body["price"], pay_url=body["pay_url"])
        if response.is_error:
            try:
                body = response.json()
                error, message = body.get("error", "unknown"), body.get("message", response.text)
            except ValueError:
                error, message = "unknown", response.text
            raise ApiError(response.status_code, error, message)
        return response

    async def start_game(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        intention: str | None = None,
    ) -> StartGameResponse:
        response = await self._request(
            "POST",
            "/game/start",
            json={
                "telegram_id": telegram_id,
                "username": username,
                "first_name": first_name,
                "intention": intention,
            },
        )
        return StartGameResponse.model_validate(response.json())

    async def set_intention(self, game_id: str, intention: str) -> SetIntentionResponse:
        response = await self._request(
            "POST", f"/game/{game_id}/intention", json={"intention": intention}
        )
        return SetIntentionResponse.model_validate(response.json())

    async def roll(self, game_id: str, idempotency_key: str | None = None) -> RollResponse:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = await self._request("POST", f"/game/{game_id}/roll", headers=headers)
        return RollResponse.model_validate(response.json())

    async def skip(self, game_id: str) -> None:
        await self._request("POST", f"/game/{game_id}/skip")

    async def respond(self, game_id: str, text: str) -> RespondResponse:
        response = await self._request("POST", f"/game/{game_id}/respond", json={"text": text})
        return RespondResponse.model_validate(response.json())

    async def get_game(self, game_id: str) -> GameStateResponse:
        response = await self._request("GET", f"/game/{game_id}")
        return GameStateResponse.model_validate(response.json())

    async def finish_game(self, game_id: str) -> FinishGameResponse:
        response = await self._request("POST", f"/game/{game_id}/finish")
        return FinishGameResponse.model_validate(response.json())

    async def get_user_status(self, telegram_id: int) -> UserStatusResponse:
        response = await self._request("GET", f"/users/{telegram_id}/status")
        return UserStatusResponse.model_validate(response.json())

    async def admin_grant(self, telegram_id: int, count: int = 1) -> AdminGrantResponse:
        response = await self._request(
            "POST", "/admin/grant", json={"telegram_id": telegram_id, "count": count}
        )
        return AdminGrantResponse.model_validate(response.json())
