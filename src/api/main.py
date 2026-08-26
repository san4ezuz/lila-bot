import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.routers import game, health
from src.services.game_service import GameNotActiveError, GameNotFoundError

logger = structlog.get_logger()

app = FastAPI(title="Lila Game API")

app.include_router(health.router)
app.include_router(game.router)

# Note: POST /payments/tribute/webhook is added in Stage 8 (docs/PLAN.md §11) together with
# webhook signature verification — deliberately not stubbed here to avoid a half-finished,
# unverified payment endpoint existing in the meantime.


@app.exception_handler(GameNotFoundError)
async def _game_not_found_handler(request: Request, exc: GameNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "game_not_found", "message": str(exc)})


@app.exception_handler(GameNotActiveError)
async def _game_not_active_handler(request: Request, exc: GameNotActiveError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"error": "game_not_active", "message": str(exc)})
