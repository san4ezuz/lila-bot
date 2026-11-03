from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db, require_internal_token
from src.api.schemas import StatsResponse, TrackEventAck, TrackEventRequest
from src.services.analytics_service import AnalyticsService

router = APIRouter(dependencies=[Depends(require_internal_token)])


@router.post("/events", response_model=TrackEventAck)
async def track_event(body: TrackEventRequest, db: AsyncSession = Depends(get_db)) -> TrackEventAck:
    await AnalyticsService(db).track(body.telegram_id, body.name, dict(body.props))
    return TrackEventAck()


@router.get("/admin/stats", response_model=StatsResponse)
async def stats(
    days: int = Query(default=1, ge=1, le=90), db: AsyncSession = Depends(get_db)
) -> StatsResponse:
    """Who may call this is the bot's job (/stats is for ADMIN_TELEGRAM_IDS only), as with
    /admin/grant."""
    return await AnalyticsService(db).funnel(days)
