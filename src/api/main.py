import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.routers import game, health, payments
from src.config import get_settings
from src.services.billing_service import InvalidWebhookSignatureError
from src.services.game_service import GameNotActiveError, GameNotFoundError

logger = structlog.get_logger()

settings = get_settings()
settings.ensure_production_secrets()

# The interactive docs would publish the whole internal API surface of a public service.
docs_url = None if settings.is_production else "/docs"
app = FastAPI(title="Lila Game API", docs_url=docs_url, redoc_url=None, openapi_url=docs_url and "/openapi.json")

app.include_router(health.router)
app.include_router(game.router)
app.include_router(payments.webhook_router)
app.include_router(payments.admin_router)


@app.exception_handler(GameNotFoundError)
async def _game_not_found_handler(request: Request, exc: GameNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "game_not_found", "message": str(exc)})


@app.exception_handler(GameNotActiveError)
async def _game_not_active_handler(request: Request, exc: GameNotActiveError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"error": "game_not_active", "message": str(exc)})


@app.exception_handler(InvalidWebhookSignatureError)
async def _invalid_signature_handler(request: Request, exc: InvalidWebhookSignatureError) -> JSONResponse:
    return JSONResponse(
        status_code=401, content={"error": "invalid_signature", "message": "webhook signature invalid"}
    )
