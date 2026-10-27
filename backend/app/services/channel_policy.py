"""Shared clock and delivery spacing for the dashboard and every publisher."""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from backend.app.models import (
    AppSetting,
    BotCampaignMessage,
    ChannelEditorialMessage,
    CouponPublication,
    Publication,
)


async def channel_window(session, minimum_minutes: int = 0) -> tuple[datetime | None, int]:
    setting = await session.scalar(select(AppSetting).where(AppSetting.key == "channel_experience"))
    minutes = max(minimum_minutes, int((setting.value if setting else {}).get("global_spacing_minutes", 15)))
    moments = [await session.scalar(select(func.max(func.coalesce(Publication.published_at, Publication.created_at))).where(
        Publication.delivery_status.in_(["SENT", "SENDING", "UNCERTAIN"]))) ]
    for model in (CouponPublication, BotCampaignMessage, ChannelEditorialMessage):
        moments.append(await session.scalar(select(func.max(func.coalesce(model.published_at, model.created_at)))))
    last = max((value for value in moments if value is not None), default=None)
    return last, minutes


async def spacing_available(session, now: datetime, minimum_minutes: int = 0) -> bool:
    last, minutes = await channel_window(session, minimum_minutes)
    return last is None or last + timedelta(minutes=minutes) <= now
