from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.app.models import AppSetting, DiscoverySchedule


async def claim_discovery(session, strategy: str) -> bool:
    now = datetime.now(UTC)
    setting = await session.scalar(select(AppSetting).where(AppSetting.key == "cadence"))
    cadence = setting.value if setting else {}
    minutes = max(1, int(cadence.get("discovery_minutes" if strategy == "relevant" else "specialized_minutes", 10 if strategy == "relevant" else 20)))
    await session.execute(insert(DiscoverySchedule).values(strategy=strategy,
        next_due_at=now, interval_minutes=minutes).on_conflict_do_nothing())
    row = await session.get(DiscoverySchedule, strategy, with_for_update=True)
    if row.interval_minutes != minutes:
        row.interval_minutes = minutes
        row.next_due_at = (row.last_started_at or now) + timedelta(minutes=minutes)
    if row.next_due_at > now:
        await session.commit()
        return False
    row.last_started_at = now
    row.next_due_at = now + timedelta(minutes=minutes)
    await session.commit()
    return True
