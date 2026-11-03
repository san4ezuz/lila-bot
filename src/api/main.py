import time
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from src.api.routers import analytics, game, health, payments
from src.config import get_settings
from src.logging_config import configure_logging
from src.services.alerts import fire_alert
from src.services.billing_service import InvalidWebhookSignatureError
from src.services.game_service import GameNotActiveError, GameNotFoundError

configure_logging()
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
app.include_router(analytics.router)


@app.middleware("http")
async def _log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    started = time.monotonic()
    response = await call_next(request)
    logger.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return response


@app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Any 5xx: logged with the traceback and sent to the admins (throttled per route)."""
    logger.error("unhandled error", path=request.url.path, exc_info=exc)
    route = getattr(request.scope.get("route"), "path", request.url.path)
    fire_alert(
        f"5xx:{route}",
        f"API 500 на {request.method} {request.url.path}: {type(exc).__name__}: {exc}",
    )
    return JSONResponse(status_code=500, content={"error": "internal", "message": "internal error"})


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
