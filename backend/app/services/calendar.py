"""Durable publication slots shared by manual and scheduled channel deliveries."""
import hashlib
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from backend.app.models import (
    AppSetting,
    BotCampaignMessage,
    ChannelEditorialMessage,
    Coupon,
    CouponPublication,
    EditorialSlot,
    Publication,
)

current_slot = ContextVar("current_editorial_slot", default=None)
TERMINAL = {"PUBLISHED", "SKIPPED", "EXPIRED", "RECONCILIATION"}
TIMED = {"_dispatch_bot_promo": "BOT_PROMO", "_dispatch_trust_reminder": "TRUST_REMINDER",
    "_dispatch_bad_offer": "BAD_OFFER"}


def scheduled_slots(values, now):
    local = now.astimezone(ZoneInfo("America/Sao_Paulo"))
    specs = []
    bot, bad, channel = (values.get(key, {}) for key in ("bot", "bad_offers", "channel_experience"))
    policies = [
        ("BOT_PROMO", bot, bot.get("enabled", True) and bot.get("promo_enabled", True), bot.get("promo_times", ["10:30", "15:30", "20:30"])),
        ("BAD_OFFER", bad, bad.get("enabled", True), bad.get("times", ["12:30", "19:30"])),
        ("TRUST_REMINDER", channel, channel.get("weekly_enabled", True), [channel.get("weekly_time", "18:00")]),
    ]
    for offset in range(-1, 8):
        day = local + timedelta(days=offset)
        for kind, policy, enabled, times in policies:
            if kind == "TRUST_REMINDER" and day.isoweekday() != int(policy.get("weekly_day", 7)):
                continue
            for clock in times:
                hour, minute = map(int, str(clock).split(":"))
                at = day.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(UTC)
                key = f"{kind}:{day:%Y-%m-%d}:{hour:02}:{minute:02}"
                deadline = at + timedelta(minutes=int(policy.get("retry_window_minutes", 90)))
                state = "SKIPPED" if not enabled else "EXPIRED" if deadline < now else "PENDING"
                specs.append(dict(slot_key=key, kind=kind, scheduled_for=at, deadline_at=deadline,
                    next_attempt_at=at, state=state, outcome_reason="DISABLED" if not enabled else "DEADLINE_ELAPSED" if state == "EXPIRED" else None))
    return specs


async def sync_calendar(session, now=None):
    now = now or datetime.now(UTC)
    settings = list(await session.scalars(select(AppSetting)))
    values = {s.key: s.value for s in settings}
    specs = scheduled_slots(values, now)
    for spec in specs:
        await session.execute(insert(EditorialSlot).values(**spec).on_conflict_do_nothing(index_elements=["slot_key"]))
    keys = {spec["slot_key"] for spec in specs if spec["state"] == "PENDING"}
    # Invalidate future slots removed/disabled by a configuration change.
    await session.execute(update(EditorialSlot).where(EditorialSlot.state == "PENDING",
        EditorialSlot.kind.in_(TIMED.values()), EditorialSlot.scheduled_for >= now,
        EditorialSlot.slot_key.not_in(keys)).values(state="SKIPPED", outcome_reason="CONFIGURATION_CHANGED"))
    # A future configuration may re-enable a slot, but never resurrect an already due one.
    await session.execute(update(EditorialSlot).where(EditorialSlot.state == "SKIPPED",
        EditorialSlot.scheduled_for > now, EditorialSlot.slot_key.in_(keys),
        EditorialSlot.outcome_reason.in_(["DISABLED", "CONFIGURATION_CHANGED"])
    ).values(state="PENDING", outcome_reason=None))
    await session.execute(update(EditorialSlot).where(EditorialSlot.state == "PENDING",
        EditorialSlot.deadline_at < now).values(state="EXPIRED", outcome_reason="DEADLINE_ELAPSED"))
    return specs


async def slot_for_call(session, name, arguments, now):
    if name in TIMED and not arguments.get("force"):
        await sync_calendar(session, now)
        return await session.scalar(select(EditorialSlot).where(EditorialSlot.kind == TIMED[name],
            EditorialSlot.state == "PENDING", EditorialSlot.scheduled_for <= now,
            EditorialSlot.deadline_at >= now, EditorialSlot.next_attempt_at <= now
        ).order_by(EditorialSlot.scheduled_for).limit(1).with_for_update())
    if name == "_publish_ready_offer":
        kind, identity = "GOOD_OFFER", arguments["queue_id"]
    elif name == "_publish_coupon":
        coupon = await session.get(Coupon, uuid.UUID(arguments["coupon_id"]))
        kind, identity = "COUPON", f"{arguments['coupon_id']}:{coupon.rules_version if coupon else 'missing'}"
    elif name == "_publish_channel_manifest":
        stored = await session.scalar(select(AppSetting).where(AppSetting.key == "channel_experience"))
        kind, identity = "MANIFEST", str(stored.version if stored else 1)
    elif name in TIMED:
        kind, identity = TIMED[name], f"manual:{now.astimezone(ZoneInfo('America/Sao_Paulo')):%Y-%m-%d}"
    else:
        return None
    key = f"{kind}:{hashlib.sha256(str(identity).encode()).hexdigest()[:40]}"
    await session.execute(insert(EditorialSlot).values(slot_key=key, kind=kind, state="PENDING",
        scheduled_for=now, deadline_at=now+timedelta(hours=24), next_attempt_at=now
    ).on_conflict_do_nothing(index_elements=["slot_key"]))
    return await session.scalar(select(EditorialSlot).where(EditorialSlot.slot_key == key).with_for_update())


def record_outcome(slot, result, now):
    if slot.state == "PUBLISHED":
        return  # A confirmed transaction is never demoted by subsequent logging failure.
    reason = str(result.get("reason") or ("SENT" if result.get("published") else "NO_DELIVERY")).upper()
    slot.outcome_reason = reason[:120]
    if result.get("published") or reason in {"SENT", "ALREADY_PUBLISHED", "SLOT_COMPLETED"}:
        slot.state = "PUBLISHED"
    elif reason in {"UNCERTAIN", "SENDING", "DELIVERY_UNCERTAIN", "INTERRUPTED", "UNEXPECTED_ERROR"}:
        slot.state = "RECONCILIATION"
    elif reason in {"GLOBAL_SPACING", "CHANNEL_BUSY", "INSUFFICIENT_EVIDENCE", "BROWSER_BUSY", "COUPON_STALE", "PRODUCT_PUBLICATION_LOCKED", "EDITORIAL_DIVERSITY"}:
        slot.state = "PENDING" if now < slot.deadline_at else "EXPIRED"
        slot.next_attempt_at = min(now+timedelta(minutes=5), slot.deadline_at)
    else:
        slot.state = "SKIPPED"


async def reconcile_confirmed(session, name, arguments, slot):
    """Only local confirmed IDs are evidence. Never retry an ambiguous HTTP send."""
    intent = None
    if name == "_publish_ready_offer":
        intent = await session.scalar(select(Publication).where(Publication.queue_id == uuid.UUID(arguments["queue_id"])))
        if intent and intent.delivery_status == "SENT" and intent.telegram_message_id:
            slot.publication_id = intent.id
    elif name == "_publish_coupon":
        coupon = await session.get(Coupon, uuid.UUID(arguments["coupon_id"]))
        if coupon:
            intent = await session.scalar(select(CouponPublication).where(CouponPublication.coupon_id == coupon.id,
                CouponPublication.rules_version == coupon.rules_version))
    elif name in {"_dispatch_bot_promo", "_dispatch_trust_reminder"}:
        local = slot.scheduled_for.astimezone(ZoneInfo("America/Sao_Paulo"))
        key = f"trust:{local:%G-W%V}" if name == "_dispatch_trust_reminder" else f"{local:%Y-%m-%d}:{local:%H:%M}"
        intent = await session.scalar(select(BotCampaignMessage).where(BotCampaignMessage.slot_key == key))
    elif name == "_publish_channel_manifest":
        intent = await session.scalar(select(ChannelEditorialMessage).where(ChannelEditorialMessage.message_key == "trust-manifest"))
    if intent and intent.published_at and intent.telegram_message_id:
        slot.state = "PUBLISHED"
        slot.outcome_reason = "CONFIRMED_DATABASE_RECEIPT"
        return True
    return False
